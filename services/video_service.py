import os
import logging
import asyncio
import json
import base64
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

# Lazy imports/availabilities
WHISPER_AVAILABLE = False
MOVIEPY_AVAILABLE = False
OPENCV_AVAILABLE = False

def check_dependencies():
    global WHISPER_AVAILABLE, MOVIEPY_AVAILABLE, OPENCV_AVAILABLE
    try:
        from faster_whisper import WhisperModel
        WHISPER_AVAILABLE = True
    except ImportError:
        WHISPER_AVAILABLE = False
        logger.warning("faster-whisper not installed; transcription will be unavailable.")

    try:
        import moviepy
        MOVIEPY_AVAILABLE = True
    except ImportError:
        MOVIEPY_AVAILABLE = False
        logger.warning("moviepy not installed; video metadata extraction and editing will be unavailable.")

    try:
        import cv2
        OPENCV_AVAILABLE = True
    except ImportError:
        OPENCV_AVAILABLE = False
        logger.warning("opencv-python not installed; keyframe extraction will be unavailable.")

# Initialize status
check_dependencies()

def parse_llm_json(response_text: str) -> dict:
    """Parse JSON robustly from LLM response text.
    Handles thinking tokens, markdown formatting, and extracts JSON content.
    """
    from src.text_helpers import strip_think
    import re
    
    # 1. Strip reasoning/thinking tokens
    cleaned = strip_think(response_text or "", prose=False, prompt_echo=False).strip()
    
    # 2. Clean standard markdown fence blocks
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.MULTILINE).strip()
    
    # 3. Try to parse directly
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    
    # 4. Fallback: Search for the first { or [ and last } or ]
    match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", cleaned)
    if match:
        json_str = match.group(0)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
            
    # 5. Last resort fallback
    logger.warning(f"Failed to parse JSON from LLM output: {response_text[:300]}...")
    return {}


class VideoService:
    """Service to handle video metadata extraction, audio transcription, keyframe extraction, and content analysis."""
    
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        self.upload_dir = os.path.join(data_dir, "video", "uploads")
        self.keyframe_dir = os.path.join(data_dir, "video", "keyframes")
        self.render_dir = os.path.join(data_dir, "video", "renders")
        
        # Ensure directories exist
        os.makedirs(self.upload_dir, exist_ok=True)
        os.makedirs(self.keyframe_dir, exist_ok=True)
        os.makedirs(self.render_dir, exist_ok=True)

    def get_video_metadata_sync(self, clip_path: str) -> dict:
        if not MOVIEPY_AVAILABLE:
            raise ImportError("moviepy is not installed. Please install moviepy.")
        
        from moviepy.video.io.VideoFileClip import VideoFileClip
        
        try:
            clip = VideoFileClip(clip_path)
            metadata = {
                "duration": float(clip.duration),
                "width": int(clip.size[0]),
                "height": int(clip.size[1]),
                "fps": float(clip.fps) if clip.fps else 0.0,
                "has_audio": clip.audio is not None,
            }
            clip.close()
            return metadata
        except Exception as e:
            logger.error(f"Failed to read video metadata for {clip_path}: {e}")
            raise ValueError(f"Failed to read video metadata: {str(e)}")

    async def get_video_metadata(self, clip_path: str) -> dict:
        return await asyncio.to_thread(self.get_video_metadata_sync, clip_path)

    def extract_audio_sync(self, clip_path: str, output_path: str) -> str:
        if not MOVIEPY_AVAILABLE:
            raise ImportError("moviepy is not installed. Please install moviepy.")
        
        from moviepy.video.io.VideoFileClip import VideoFileClip
        
        try:
            clip = VideoFileClip(clip_path)
            if clip.audio is None:
                clip.close()
                raise ValueError("Video clip does not contain any audio track.")
            
            clip.audio.write_audiofile(output_path, logger=None)
            clip.close()
            return output_path
        except Exception as e:
            logger.error(f"Failed to extract audio from {clip_path}: {e}")
            raise ValueError(f"Failed to extract audio: {str(e)}")

    async def extract_audio(self, clip_path: str, output_path: str) -> str:
        return await asyncio.to_thread(self.extract_audio_sync, clip_path, output_path)

    def transcribe_audio_sync(self, audio_path: str, model_size: str = "base") -> dict:
        if not WHISPER_AVAILABLE:
            raise ImportError("faster-whisper is not installed. Please install faster-whisper.")
        
        from faster_whisper import WhisperModel
        
        try:
            # Safe CPU run defaults
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            segments, info = model.transcribe(audio_path, beam_size=5)
            
            formatted_segments = []
            for segment in segments:
                formatted_segments.append({
                    "start": float(segment.start),
                    "end": float(segment.end),
                    "text": segment.text.strip(),
                    "timestamp": f"{int(segment.start // 60):02d}:{int(segment.start % 60):02d}",
                })
                
            full_text = " ".join(seg["text"] for seg in formatted_segments)
            
            return {
                "success": True,
                "transcript": full_text,
                "segments": formatted_segments,
                "language": info.language,
                "language_probability": info.language_probability,
            }
        except Exception as e:
            logger.error(f"Whisper transcription failed: {e}")
            raise ValueError(f"Transcription failed: {str(e)}")

    async def transcribe_audio(self, audio_path: str, model_size: str = "base") -> dict:
        return await asyncio.to_thread(self.transcribe_audio_sync, audio_path, model_size)

    def extract_keyframes_sync(self, clip_path: str, output_dir: str, interval_sec: float = 2.0) -> List[str]:
        if not OPENCV_AVAILABLE:
            raise ImportError("opencv-python is not installed. Please install opencv-python.")
        
        import cv2
        
        try:
            os.makedirs(output_dir, exist_ok=True)
            cap = cv2.VideoCapture(clip_path)
            fps = cap.get(cv2.CAP_PROP_FPS)
            if fps <= 0:
                fps = 30.0
            
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            frame_interval = int(fps * interval_sec)
            if frame_interval <= 0:
                frame_interval = 1
            
            keyframe_paths = []
            frame_idx = 0
            
            while cap.isOpened():
                ret, frame = cap.read()
                if not ret:
                    break
                
                if frame_idx % frame_interval == 0:
                    sec = frame_idx / fps
                    filename = f"keyframe_{int(sec):04d}s.jpg"
                    filepath = os.path.join(output_dir, filename)
                    cv2.imwrite(filepath, frame)
                    # Use absolute path or relative to workspace root
                    keyframe_paths.append(os.path.abspath(filepath))
                    
                frame_idx += 1
                
            cap.release()
            return keyframe_paths
        except Exception as e:
            logger.error(f"Failed to extract keyframes from {clip_path}: {e}")
            raise ValueError(f"Keyframe extraction failed: {str(e)}")

    async def extract_keyframes(self, clip_path: str, output_dir: str, interval_sec: float = 2.0) -> List[str]:
        return await asyncio.to_thread(self.extract_keyframes_sync, clip_path, output_dir, interval_sec)

    async def generate_visual_summary(self, keyframe_paths: List[str], owner: str = None) -> str:
        """Send keyframes to the configured LLM for visual summary.
        If the model doesn't support vision, or the call fails, falls back gracefully to a text-only summary.
        """
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async
        
        # 1. Resolve model endpoint
        url, model, headers = resolve_endpoint("vision", owner=owner)
        if not url or not model:
            url, model, headers = resolve_endpoint("default", owner=owner)
            
        if not url or not model:
            logger.warning("No LLM endpoint resolved for visual summary.")
            return "No visual summary available (LLM endpoint not configured)."
            
        content = [{"type": "text", "text": "Describe the visual contents of this video based on these keyframes extracted at regular intervals. Provide a cohesive summary of the scene, setting, subjects, and pacing. Keep it under 200 words."}]
        
        sampled_keyframes = keyframe_paths[:5] if len(keyframe_paths) > 5 else keyframe_paths
        
        for kp in sampled_keyframes:
            if not os.path.exists(kp):
                continue
            try:
                with open(kp, "rb") as image_file:
                    encoded_string = base64.b64encode(image_file.read()).decode('utf-8')
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{encoded_string}"
                    }
                })
            except Exception as e:
                logger.warning(f"Failed to read/encode keyframe {kp}: {e}")
                
        try:
            messages = [{"role": "user", "content": content}]
            response = await llm_call_async(
                url=url,
                model=model,
                messages=messages,
                headers=headers,
                temperature=0.3,
                max_tokens=500,
                timeout=60
            )
            if response:
                from src.text_helpers import strip_think
                return strip_think(response).strip()
        except Exception as e:
            logger.warning(f"Multimodal vision call failed (likely model does not support image inputs): {e}")
            
        return "Visual summary unavailable (Multimodal analysis not supported by the current model configuration)."

    async def analyze_content_format(self, transcript: str, duration: float, owner: str = None) -> dict:
        """Ask the LLM to recommend short-form vs long-form vs both, with reasoning."""
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async
        
        url, model, headers = resolve_endpoint("utility", owner=owner)
        if not url or not model:
            url, model, headers = resolve_endpoint("default", owner=owner)
            
        if not url or not model:
            return {
                "format": "long",
                "reasoning": "Fallback default: No LLM configured."
            }
            
        truncated_transcript = transcript[:8000] + "... [truncated]" if len(transcript) > 8000 else transcript
        
        prompt = (
            "You are an expert video producer and AI content strategist.\n"
            "Analyze the following video details and recommend whether this content is best suited for:\n"
            "- 'short' (under 60s vertical format, e.g. Shorts/TikTok/Reels)\n"
            "- 'long' (traditional horizontal format)\n"
            "- 'both' (can be publishable as both)\n\n"
            f"Video Duration: {duration:.1f} seconds\n"
            f"Transcript:\n{truncated_transcript}\n\n"
            "Return ONLY raw JSON in the following format (no extra keys, no markdown wrappers):\n"
            '{"format": "short|long|both", "reasoning": "detailed explanation of why"}'
        )
        
        try:
            response = await llm_call_async(
                url=url,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                headers=headers,
                temperature=0.1,
                max_tokens=500,
                timeout=60
            )
            
            parsed = parse_llm_json(response)
            if parsed and "format" in parsed:
                return parsed
        except Exception as e:
            logger.error(f"Format analysis failed: {e}")
            
        # Fallback default
        recommended = "long"
        if duration < 60.0:
            recommended = "short"
            
        return {
            "format": recommended,
            "reasoning": f"Fallback default recommendation based on duration of {duration:.1f} seconds."
        }

    async def analyze_project(self, project_id: int, owner: str = None) -> dict:
        from core.database import SessionLocal, VideoProject, VideoClip
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async
        
        db = SessionLocal()
        try:
            project = db.query(VideoProject).filter(VideoProject.id == project_id)
            if owner is not None:
                project = project.filter(VideoProject.owner == owner)
            project = project.first()
            if not project:
                raise ValueError("Project not found")
                
            # Aggregate clips data
            clips = db.query(VideoClip).filter(VideoClip.project_id == project_id).order_by(VideoClip.position.asc()).all()
            if not clips:
                return {"error": "No clips assigned to this project."}
                
            total_duration = sum(c.duration_seconds or 0.0 for c in clips)
            aggregated_transcript = []
            aggregated_visuals = []
            
            for i, c in enumerate(clips):
                c_name = c.file_name or f"Clip {i+1}"
                if c.transcript:
                    aggregated_transcript.append(f"[{c_name}] (duration: {c.duration_seconds or 0.0:.1f}s):\n{c.transcript}")
                if c.visual_summary and "unavailable" not in c.visual_summary:
                    aggregated_visuals.append(f"[{c_name}]:\n{c.visual_summary}")
                    
            transcript_text = "\n\n".join(aggregated_transcript)
            visuals_text = "\n\n".join(aggregated_visuals)
            
            # Resolve endpoint
            url, model, headers = resolve_endpoint("utility", owner=owner)
            if not url or not model:
                url, model, headers = resolve_endpoint("default", owner=owner)
                
            if not url or not model:
                return {"error": "No LLM endpoint configured."}
                
            prompt = (
                "You are an expert AI video content strategist and editor.\n"
                "Analyze the following aggregated details from the source clips of a video project to recommend publishing options and content highlights.\n\n"
                f"Project Title: {project.title}\n"
                f"Total Duration: {total_duration:.1f} seconds\n"
                f"Aggregated Clips Transcripts:\n{transcript_text[:8000]}\n\n"
                f"Aggregated Clips Visual Summaries:\n{visuals_text[:4000]}\n\n"
                "Recommend the best publishing format:\n"
                "- 'short' (vertical under 60s, e.g. TikTok, Shorts, Reels)\n"
                "- 'long' (traditional horizontal video)\n"
                "- 'both' (can be produced in both formats)\n\n"
                "Generate suggestions for YouTube title, description, and tags.\n\n"
                "Return ONLY raw JSON in the following schema (no extra keys, no markdown wrappers):\n"
                "{\n"
                '  "format_recommendation": "short|long|both",\n'
                '  "format_reasoning": "detailed explanation of why",\n'
                '  "suggested_title": "suggested youtube title",\n'
                '  "suggested_description": "suggested youtube description with timestamps/summary",\n'
                '  "suggested_tags": ["tag1", "tag2", "tag3"],\n'
                '  "highlights": "bullet list of top moments or key topics discussed"\n'
                "}"
            )
            
            response = await llm_call_async(
                url=url,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                headers=headers,
                temperature=0.3,
                max_tokens=1000,
                timeout=90
            )
            
            parsed = parse_llm_json(response)
            if parsed:
                # Update project
                project.format_recommendation = parsed.get("format_recommendation", "long")
                project.format_reasoning = parsed.get("format_reasoning", "")
                project.description = parsed.get("highlights", "")
                
                # Update publish settings
                pub_settings = project.publish_settings or {}
                pub_settings.update({
                    "title": parsed.get("suggested_title", project.title),
                    "description": parsed.get("suggested_description", ""),
                    "tags": parsed.get("suggested_tags", []),
                })
                project.publish_settings = pub_settings
                db.commit()
                
                return parsed
            else:
                return {"error": "Failed to parse LLM analysis response."}
        finally:
            db.close()

    async def suggest_edit_plan(self, project_id: int, owner: str = None) -> dict:
        from core.database import SessionLocal, VideoProject, VideoClip
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async
        
        db = SessionLocal()
        try:
            project = db.query(VideoProject).filter(VideoProject.id == project_id)
            if owner is not None:
                project = project.filter(VideoProject.owner == owner)
            project = project.first()
            if not project:
                raise ValueError("Project not found")
                
            # Aggregate clips data
            clips = db.query(VideoClip).filter(VideoClip.project_id == project_id).order_by(VideoClip.position.asc()).all()
            if not clips:
                return {"error": "No clips assigned to this project."}
                
            aggregated_transcript = []
            for i, c in enumerate(clips):
                c_name = c.file_name or f"Clip {i+1}"
                if c.transcript_segments:
                    segments_list = c.transcript_segments
                    if isinstance(segments_list, str):
                        try:
                            segments_list = json.loads(segments_list)
                        except Exception:
                            segments_list = []
                    segments_str = "\n".join(
                        f"[{seg.get('timestamp', '00:00')}] ({seg.get('start', 0.0):.1f}s - {seg.get('end', 0.0):.1f}s): {seg.get('text', '')}"
                        for seg in segments_list
                    )
                    aggregated_transcript.append(f"[{c_name}] (ID: {c.id}):\n{segments_str}")
                elif c.transcript:
                    aggregated_transcript.append(f"[{c_name}] (ID: {c.id}):\n{c.transcript}")
                    
            transcript_text = "\n\n".join(aggregated_transcript)
            
            # Resolve endpoint
            url, model, headers = resolve_endpoint("utility", owner=owner)
            if not url or not model:
                url, model, headers = resolve_endpoint("default", owner=owner)
                
            if not url or not model:
                return {"error": "No LLM endpoint configured."}
                
            prompt = (
                "You are an expert AI video editor.\n"
                "Based on the following timestamped transcripts from the clips, generate a detailed step-by-step editing plan.\n"
                "Specifically, identify the key parts/segments to keep, cut, or speed up, where to place text overlays, and "
                "recommend transitions.\n\n"
                f"Project Title: {project.title}\n"
                f"Aggregated Clips Transcripts with Timestamps:\n{transcript_text[:8000]}\n\n"
                "Create a structured JSON edit plan in the format below. The 'edit_instructions' key must contain a list of concrete actions to perform.\n"
                "Return ONLY raw JSON in the following schema (no extra keys, no markdown wrappers):\n"
                "{\n"
                '  "reasoning": "general summary of editing strategy",\n'
                '  "edit_instructions": [\n'
                '    {"action": "trim", "clip_id": 1, "start": 0.0, "end": 15.5},\n'
                '    {"action": "add_text", "text": "Hook line!", "position": "center", "start": 1.0, "end": 4.5},\n'
                '    {"action": "concat", "clip_ids": [1, 2], "transition": "fade"}\n'
                '  ]\n'
                "}"
            )
            
            response = await llm_call_async(
                url=url,
                model=model,
                messages=[{"role": "user", "content": prompt}],
                headers=headers,
                temperature=0.2,
                max_tokens=1200,
                timeout=90
            )
            
            parsed = parse_llm_json(response)
            if parsed:
                return parsed
            else:
                return {"error": "Failed to parse LLM edit plan response."}
        finally:
            db.close()

    def render_project_clip_sync(self, project_id: int, edit_instructions: List[dict]) -> Any:
        """Helper to build the moviepy video composition from instructions."""
        if not MOVIEPY_AVAILABLE:
            raise ImportError("moviepy is not installed.")

        from moviepy.video.io.VideoFileClip import VideoFileClip
        from moviepy.video.compositing.concatenate import concatenate_videoclips
        from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
        from moviepy.video.fx.all import crop
        
        db = SessionLocal()
        opened_clips = []
        try:
            # 1. Load clips from db
            clips_in_db = db.query(VideoClip).filter(VideoClip.project_id == project_id).order_by(VideoClip.position.asc()).all()
            clips_map = {c.id: c for c in clips_in_db}
            
            # 2. Build list of moviepy clips
            subclips_list = []
            do_vertical_crop = False
            
            for inst in edit_instructions:
                action = inst.get("action")
                if action == "crop_vertical":
                    do_vertical_crop = True
                    continue
                    
                if action in ("trim", "use_clip"):
                    c_id = inst.get("clip_id")
                    clip_data = clips_map.get(c_id)
                    if not clip_data or not os.path.exists(clip_data.file_path):
                        continue
                        
                    mv_clip = VideoFileClip(clip_data.file_path)
                    opened_clips.append(mv_clip)
                    
                    start = inst.get("start", clip_data.trim_start or 0.0)
                    end = inst.get("end", clip_data.trim_end or mv_clip.duration)
                    
                    if start < 0: start = 0.0
                    if end > mv_clip.duration: end = mv_clip.duration
                    if start < end:
                        mv_clip = mv_clip.subclip(start, end)
                        
                    subclips_list.append(mv_clip)
            
            if not subclips_list:
                for c in clips_in_db:
                    if os.path.exists(c.file_path):
                        mv_clip = VideoFileClip(c.file_path)
                        opened_clips.append(mv_clip)
                        subclips_list.append(mv_clip)
            
            if not subclips_list:
                raise ValueError("No video clips available to render.")
                
            # 3. Concatenate clips
            final_clip = concatenate_videoclips(subclips_list)
            
            # 4. Crop vertically if requested (16:9 -> 9:16)
            if do_vertical_crop:
                w, h = final_clip.size
                new_w = int(h * 9 / 16)
                x1 = int((w - new_w) / 2)
                final_clip = crop(final_clip, x1=x1, y1=0, width=new_w, height=h)
                
            # 5. Overlays (text overlays)
            text_overlays = [inst for inst in edit_instructions if inst.get("action") == "add_text"]
            if text_overlays:
                try:
                    from moviepy.video.VideoClip import TextClip
                    txt_clips = []
                    for to in text_overlays:
                        text = to.get("text", "")
                        start = to.get("start", 0.0)
                        end = to.get("end", final_clip.duration)
                        position = to.get("position", "center")
                        
                        if text:
                            try:
                                t_clip = TextClip(text, fontsize=48, color='white', font='Arial')
                                t_clip = t_clip.set_pos(position).set_start(start).set_duration(end - start)
                                txt_clips.append(t_clip)
                                opened_clips.append(t_clip)
                            except Exception as txt_ex:
                                logger.warning(f"Could not create TextClip (ImageMagick likely missing): {txt_ex}")
                    
                    if txt_clips:
                        final_clip = CompositeVideoClip([final_clip] + txt_clips)
                except ImportError:
                    logger.warning("TextClip not importable. Skipping text overlays.")
            
            return final_clip, opened_clips
        except Exception:
            # If error occurs, close what we opened
            for c in opened_clips:
                try: c.close()
                except Exception: pass
            raise
        finally:
            db.close()

    def render_final_sync(self, project_id: int, edit_instructions: List[dict], output_path: str, quality: str = "high") -> str:
        """Synchronously renders the final video. Called via asyncio.to_thread."""
        final_clip = None
        opened_clips = []
        try:
            final_clip, opened_clips = self.render_project_clip_sync(project_id, edit_instructions)
            
            fps = 30
            preset = "medium"
            if quality == "draft":
                fps = 24
                preset = "ultrafast"
                
            final_clip.write_videofile(
                output_path,
                fps=fps,
                codec="libx264",
                audio_codec="aac",
                preset=preset,
                logger=None
            )
            return output_path
        finally:
            if final_clip:
                try: final_clip.close()
                except Exception: pass
            for c in opened_clips:
                try: c.close()
                except Exception: pass

    async def render_final(self, project_id: int, edit_instructions: List[dict], output_path: str, quality: str = "high") -> str:
        return await asyncio.to_thread(self.render_final_sync, project_id, edit_instructions, output_path, quality)

    def render_preview_sync(self, project_id: int, edit_instructions: List[dict], timestamp: float, output_path: str) -> str:
        """Extracts and saves a single frame preview as a JPG."""
        final_clip = None
        opened_clips = []
        try:
            final_clip, opened_clips = self.render_project_clip_sync(project_id, edit_instructions)
            
            if timestamp > final_clip.duration:
                timestamp = final_clip.duration - 0.1
            if timestamp < 0:
                timestamp = 0.0
                
            final_clip.save_frame(output_path, t=timestamp)
            return output_path
        finally:
            if final_clip:
                try: final_clip.close()
                except Exception: pass
            for c in opened_clips:
                try: c.close()
                except Exception: pass

    async def render_preview(self, project_id: int, edit_instructions: List[dict], timestamp: float, output_path: str) -> str:
        return await asyncio.to_thread(self.render_preview_sync, project_id, edit_instructions, timestamp, output_path)
