import os
import uuid
import json
import logging
from typing import Optional, List, Dict, Any
import shutil

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from core.database import SessionLocal, VideoClip, VideoProject, YouTubeAccount
from src.auth_helpers import get_current_user
from services.video_service import VideoService

logger = logging.getLogger(__name__)

class ProjectCreate(BaseModel):
    title: str
    description: Optional[str] = None
    clip_ids: Optional[List[int]] = None

class ProjectUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    clip_ids: Optional[List[int]] = None
    status: Optional[str] = None

class ProjectEditRequest(BaseModel):
    edit_instructions: List[dict]
    quality: Optional[str] = "high"

class YouTubeAccountCreate(BaseModel):
    client_id: str
    client_secret: str

class VideoPublishRequest(BaseModel):
    account_id: int
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    privacy: Optional[str] = "private"
    category: Optional[str] = "22"
    thumbnail_path: Optional[str] = None



router = APIRouter(prefix="/api/video", tags=["video"])

# ---------------------------------------------------------------------------
# Background Task Helpers
# ---------------------------------------------------------------------------

async def run_clip_processing_pipeline_background(clip_id: int, owner: Optional[str]):
    db = SessionLocal()
    try:
        clip = db.query(VideoClip).filter(VideoClip.id == clip_id).first()
        if not clip:
            logger.error(f"Clip {clip_id} not found for background pipeline.")
            return

        video_service = VideoService()
        
        # 1. Extract metadata in the background
        try:
            metadata = await video_service.get_video_metadata(clip.file_path)
            clip.duration_seconds = metadata.get("duration", 0.0)
            clip.resolution = f"{metadata.get('width', 0)}x{metadata.get('height', 0)}"
            clip.fps = metadata.get("fps", 0.0)
            clip.has_audio = metadata.get("has_audio", True)
            db.commit()
            logger.info(f"Successfully extracted metadata in background for clip {clip_id}")
        except Exception as metadata_ex:
            logger.warning(f"Failed to extract metadata in background for clip {clip_id}: {metadata_ex}")
            
    finally:
        db.close()

    # 2. Trigger transcription and visual description background tasks sequentially
    await run_transcription_background(clip_id, owner)
    await run_visual_analysis_background(clip_id, owner)


async def run_transcription_background(clip_id: int, owner: Optional[str]):
    db = SessionLocal()
    try:
        clip = db.query(VideoClip).filter(VideoClip.id == clip_id).first()
        if not clip:
            logger.error(f"Clip {clip_id} not found for transcription.")
            return

        video_service = VideoService()
        audio_filename = f"audio_{clip_id}_{uuid.uuid4().hex[:8]}.wav"
        audio_path = os.path.abspath(os.path.join(video_service.render_dir, audio_filename))

        # 1. Extract audio from video
        try:
            await video_service.extract_audio(clip.file_path, audio_path)
        except Exception as e:
            logger.error(f"Failed to extract audio for clip {clip_id}: {e}")
            # If no audio is extractable (e.g. video has no audio track), update DB
            clip.transcript = "No audio track available."
            clip.has_audio = False
            db.commit()
            return

        # 2. Perform Whisper Transcription
        try:
            # default to 'base' model
            transcription_result = await video_service.transcribe_audio(audio_path, model_size="base")
            
            from services.video_service import clean_transcript_text
            raw_transcript = transcription_result.get("transcript", "")
            clip.transcript = clean_transcript_text(raw_transcript)
            
            raw_segments = transcription_result.get("segments", [])
            cleaned_segments = []
            for seg in raw_segments:
                seg_text = clean_transcript_text(seg.get("text", ""))
                if seg_text.strip():
                    seg["text"] = seg_text
                    cleaned_segments.append(seg)
            clip.transcript_segments = cleaned_segments
            
            clip.has_audio = True
            db.commit()
            logger.info(f"Successfully transcribed and cleaned clip {clip_id}")
        except Exception as e:
            logger.error(f"Failed to transcribe audio for clip {clip_id}: {e}")
            clip.transcript = f"Transcription failed: {str(e)}"
            db.commit()
        finally:
            if os.path.exists(audio_path):
                try:
                    os.remove(audio_path)
                except Exception as ex:
                    logger.warning(f"Could not remove temp audio file {audio_path}: {ex}")
    finally:
        db.close()


async def run_visual_analysis_background(clip_id: int, owner: Optional[str]):
    db = SessionLocal()
    try:
        clip = db.query(VideoClip).filter(VideoClip.id == clip_id).first()
        if not clip:
            logger.error(f"Clip {clip_id} not found for visual analysis.")
            return

        video_service = VideoService()
        output_dir = os.path.abspath(os.path.join(video_service.keyframe_dir, str(clip_id)))

        # 1. Extract keyframes
        try:
            keyframes = await video_service.extract_keyframes(clip.file_path, output_dir, interval_sec=2.0)
            # Store keyframe paths in scene_descriptions
            clip.scene_descriptions = keyframes
            db.commit()
        except Exception as e:
            logger.error(f"Failed to extract keyframes for clip {clip_id}: {e}")
            clip.visual_summary = f"Keyframe extraction failed: {str(e)}"
            db.commit()
            return

        # 2. Call multimodal LLM to describe keyframes
        try:
            visual_summary = await video_service.generate_visual_summary(keyframes, owner=owner, transcript=clip.transcript)
            clip.visual_summary = visual_summary
            db.commit()
            logger.info(f"Successfully generated visual summary for clip {clip_id}")
        except Exception as e:
            logger.error(f"Failed to generate visual summary for clip {clip_id}: {e}")
            clip.visual_summary = f"Visual analysis failed: {str(e)}"
            db.commit()
    finally:
        db.close()

async def run_project_render_background(project_id: int, edit_instructions: List[dict], quality: str, owner: Optional[str]):
    db = SessionLocal()
    try:
        project = db.query(VideoProject).filter(VideoProject.id == project_id).first()
        if not project:
            logger.error(f"Project {project_id} not found for rendering.")
            return
            
        video_service = VideoService()
        output_filename = f"render_{project_id}_{uuid.uuid4().hex[:8]}.mp4"
        output_path = os.path.abspath(os.path.join(video_service.render_dir, output_filename))
        
        try:
            await video_service.render_final(project_id, edit_instructions, output_path, quality)
            project.status = "ready"
            project.output_path = output_path
            db.commit()
            logger.info(f"Successfully rendered project {project_id} to {output_path}")
        except Exception as e:
            logger.error(f"Failed to render project {project_id}: {e}")
            project.status = "failed"
            db.commit()
    finally:
        db.close()

async def run_project_publish_background(
    project_id: int,
    account_id: int,
    metadata: dict,
    owner: Optional[str]
):
    db = SessionLocal()
    try:
        project = db.query(VideoProject).filter(VideoProject.id == project_id).first()
        account = db.query(YouTubeAccount).filter(YouTubeAccount.id == account_id).first()
        
        if not project or not account:
            logger.error(f"Project {project_id} or YouTube account {account_id} not found.")
            return

        if not project.output_path or not os.path.exists(project.output_path):
            logger.error(f"Rendered output for project {project_id} does not exist.")
            project.status = "failed"
            db.commit()
            return

        project.status = "publishing"
        db.commit()

        from services.youtube_service import YouTubeService
        yt_service = YouTubeService()
        
        try:
            video_id = await yt_service.upload_video(account, db, project.output_path, metadata)
            project.youtube_video_id = video_id
            project.status = "published"
            
            # Save publish settings
            pub_settings = project.publish_settings or {}
            pub_settings["youtube_video_id"] = video_id
            pub_settings["account_id"] = account_id
            pub_settings["published_metadata"] = metadata
            project.publish_settings = pub_settings
            
            db.commit()
            logger.info(f"Successfully published project {project_id} to YouTube. Video ID: {video_id}")
        except Exception as e:
            logger.error(f"Failed to publish project {project_id} to YouTube: {e}")
            project.status = "failed"
            db.commit()
    finally:
        db.close()

# ---------------------------------------------------------------------------
# Router Factory
# ---------------------------------------------------------------------------

def setup_video_routes():
    def _owner(request: Request) -> Optional[str]:
        return get_current_user(request)

    # --- UPLOAD CLIP ---
    @router.post("/upload")
    async def upload_clip(
        request: Request,
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...)
    ):
        user = _owner(request)
        video_service = VideoService()
        
        # Read project_id from form data
        form_data = await request.form()
        project_id_val = form_data.get("project_id")
        project_id = int(project_id_val) if project_id_val else None
        
        # 1. Create a unique local filename
        file_ext = os.path.splitext(file.filename)[1]
        unique_filename = f"clip_{uuid.uuid4().hex}{file_ext}"
        destination_path = os.path.abspath(os.path.join(video_service.upload_dir, unique_filename))
        
        # 2. Save file contents to disk
        try:
            with open(destination_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
        except Exception as e:
            logger.error(f"Failed to save uploaded clip: {e}")
            raise HTTPException(500, f"Failed to save upload: {str(e)}")
            
        # 3. Create clip database record
        db = SessionLocal()
        try:
            clip = VideoClip(
                owner=user,
                project_id=project_id,
                file_path=destination_path,
                file_name=file.filename,
                duration_seconds=0.0,
                resolution="Unknown",
                fps=0.0,
                has_audio=True
            )
            db.add(clip)
            db.commit()
            db.refresh(clip)
            
            # 4. Automatically trigger metadata extraction, transcription, and visual description in the background
            background_tasks.add_task(run_clip_processing_pipeline_background, clip.id, user)

            return clip.to_dict()
        finally:
            db.close()

    # --- LIST CLIPS ---
    @router.get("/clips")
    def list_clips(request: Request, project_id: Optional[int] = None):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(VideoClip)
            if user is not None:
                query = query.filter(VideoClip.owner == user)
            if project_id is not None:
                query = query.filter(VideoClip.project_id == project_id)
            clips = query.order_by(VideoClip.position.asc(), VideoClip.created_at.desc()).all()
            return {"clips": [c.to_dict() for c in clips]}
        finally:
            db.close()

    # --- GET CLIP BY ID ---
    @router.get("/clips/{clip_id}")
    def get_clip(clip_id: int, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(VideoClip).filter(VideoClip.id == clip_id)
            if user is not None:
                query = query.filter(VideoClip.owner == user)
            clip = query.first()
            if not clip:
                raise HTTPException(404, "Video clip not found")
            return clip.to_dict()
        finally:
            db.close()

    # --- DELETE CLIP ---
    @router.delete("/clips/{clip_id}")
    def delete_clip(clip_id: int, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(VideoClip).filter(VideoClip.id == clip_id)
            if user is not None:
                query = query.filter(VideoClip.owner == user)
            clip = query.first()
            if not clip:
                raise HTTPException(404, "Video clip not found")
                
            # Remove file from disk
            if os.path.exists(clip.file_path):
                try:
                    os.remove(clip.file_path)
                except Exception as e:
                    logger.warning(f"Could not remove clip file {clip.file_path}: {e}")
                    
            # Remove keyframes folder
            video_service = VideoService()
            keyframes_path = os.path.join(video_service.keyframe_dir, str(clip.id))
            if os.path.exists(keyframes_path):
                try:
                    shutil.rmtree(keyframes_path)
                except Exception as e:
                    logger.warning(f"Could not remove keyframes directory {keyframes_path}: {e}")

            db.delete(clip)
            db.commit()
            return {"success": True, "message": f"Clip {clip_id} deleted successfully."}
        finally:
            db.close()

    # --- FORCE/TRIGGER TRANSCRIBE ---
    @router.post("/clips/{clip_id}/transcribe")
    def trigger_transcription(clip_id: int, request: Request, background_tasks: BackgroundTasks):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(VideoClip).filter(VideoClip.id == clip_id)
            if user is not None:
                query = query.filter(VideoClip.owner == user)
            clip = query.first()
            if not clip:
                raise HTTPException(404, "Video clip not found")
                
            background_tasks.add_task(run_transcription_background, clip.id, user)
            return {"success": True, "message": "Transcription task added to queue."}
        finally:
            db.close()

    # --- FORCE/TRIGGER VISUAL ANALYSIS ---
    @router.post("/clips/{clip_id}/analyze-visuals")
    def trigger_visual_analysis(clip_id: int, request: Request, background_tasks: BackgroundTasks):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(VideoClip).filter(VideoClip.id == clip_id)
            if user is not None:
                query = query.filter(VideoClip.owner == user)
            clip = query.first()
            if not clip:
                raise HTTPException(404, "Video clip not found")
                
            background_tasks.add_task(run_visual_analysis_background, clip.id, user)
            return {"success": True, "message": "Visual analysis task added to queue."}
        finally:
            db.close()

    # --- GET TRANSCRIPT ---
    @router.get("/clips/{clip_id}/transcript")
    def get_transcript(clip_id: int, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(VideoClip).filter(VideoClip.id == clip_id)
            if user is not None:
                query = query.filter(VideoClip.owner == user)
            clip = query.first()
            if not clip:
                raise HTTPException(404, "Video clip not found")
            return {
                "clip_id": clip.id,
                "transcript": clip.transcript or "",
                "segments": clip.transcript_segments or []
            }
        finally:
            db.close()

    # --- CREATE PROJECT ---
    @router.post("/projects")
    def create_project(request: Request, body: ProjectCreate):
        user = _owner(request)
        db = SessionLocal()
        try:
            project = VideoProject(
                owner=user,
                title=body.title,
                description=body.description,
                status="draft"
            )
            db.add(project)
            db.commit()
            db.refresh(project)
            
            if body.clip_ids:
                for idx, c_id in enumerate(body.clip_ids):
                    clip = db.query(VideoClip).filter(VideoClip.id == c_id)
                    if user is not None:
                        clip = clip.filter(VideoClip.owner == user)
                    clip = clip.first()
                    if clip:
                        clip.project_id = project.id
                        clip.position = idx
                db.commit()
                
            return project.to_dict()
        finally:
            db.close()

    # --- LIST PROJECTS ---
    @router.get("/projects")
    def list_projects(request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(VideoProject)
            if user is not None:
                query = query.filter(VideoProject.owner == user)
            projects = query.order_by(VideoProject.created_at.desc()).all()
            return {"projects": [p.to_dict() for p in projects]}
        finally:
            db.close()

    # --- GET PROJECT ---
    @router.get("/projects/{project_id}")
    def get_project(project_id: int, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(VideoProject).filter(VideoProject.id == project_id)
            if user is not None:
                query = query.filter(VideoProject.owner == user)
            project = query.first()
            if not project:
                raise HTTPException(404, "Project not found")
                
            clips = db.query(VideoClip).filter(VideoClip.project_id == project_id).order_by(VideoClip.position.asc()).all()
            
            res = project.to_dict()
            res["clips"] = [c.to_dict() for c in clips]
            return res
        finally:
            db.close()

    # --- UPDATE PROJECT ---
    @router.put("/projects/{project_id}")
    def update_project(project_id: int, request: Request, body: ProjectUpdate):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(VideoProject).filter(VideoProject.id == project_id)
            if user is not None:
                query = query.filter(VideoProject.owner == user)
            project = query.first()
            if not project:
                raise HTTPException(404, "Project not found")
                
            if body.title is not None:
                project.title = body.title
            if body.description is not None:
                project.description = body.description
            if body.status is not None:
                project.status = body.status
                
            if body.clip_ids is not None:
                db.query(VideoClip).filter(VideoClip.project_id == project_id).update({"project_id": None})
                
                for idx, c_id in enumerate(body.clip_ids):
                    clip = db.query(VideoClip).filter(VideoClip.id == c_id)
                    if user is not None:
                        clip = clip.filter(VideoClip.owner == user)
                    clip = clip.first()
                    if clip:
                        clip.project_id = project_id
                        clip.position = idx
                        
            db.commit()
            return project.to_dict()
        finally:
            db.close()

    # --- DELETE PROJECT ---
    @router.delete("/projects/{project_id}")
    def delete_project(project_id: int, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(VideoProject).filter(VideoProject.id == project_id)
            if user is not None:
                query = query.filter(VideoProject.owner == user)
            project = query.first()
            if not project:
                raise HTTPException(404, "Project not found")
                
            db.query(VideoClip).filter(VideoClip.project_id == project_id).update({"project_id": None})
            
            db.delete(project)
            db.commit()
            return {"success": True, "message": f"Project {project_id} deleted."}
        finally:
            db.close()

    # --- ANALYZE PROJECT ---
    @router.post("/projects/{project_id}/analyze")
    async def analyze_project_endpoint(project_id: int, request: Request):
        user = _owner(request)
        video_service = VideoService()
        try:
            result = await video_service.analyze_project(project_id, owner=user)
            if "error" in result and not result.get("fallback"):
                raise HTTPException(500, result["error"])
            return result
        except ValueError as ex:
            raise HTTPException(404, str(ex))
        except Exception as e:
            logger.error(f"Analysis error: {e}")
            raise HTTPException(500, str(e))

    # --- EDIT PLAN ---
    @router.post("/projects/{project_id}/edit-plan")
    async def suggest_edit_plan_endpoint(project_id: int, request: Request):
        user = _owner(request)
        style_opts = []
        custom_prompt = None
        try:
            body = await request.json()
            if body:
                style_opts = body.get("style_opts", [])
                custom_prompt = body.get("custom_prompt", None)
        except Exception:
            pass

        video_service = VideoService()
        try:
            result = await video_service.suggest_edit_plan(
                project_id, 
                owner=user, 
                style_opts=style_opts, 
                custom_prompt=custom_prompt
            )
            if "error" in result and not result.get("fallback"):
                raise HTTPException(500, result["error"])
            return result
        except ValueError as ex:
            raise HTTPException(404, str(ex))
        except Exception as e:
            logger.error(f"Edit plan error: {e}")
            raise HTTPException(500, str(e))

    # --- SAVE EDITS ---
    @router.post("/projects/{project_id}/edit")
    def apply_project_edits(project_id: int, request: Request, body: ProjectEditRequest):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(VideoProject).filter(VideoProject.id == project_id)
            if user is not None:
                query = query.filter(VideoProject.owner == user)
            project = query.first()
            if not project:
                raise HTTPException(404, "Project not found")
                
            pub_settings = project.publish_settings or {}
            pub_settings["edit_instructions"] = body.edit_instructions
            project.publish_settings = pub_settings
            db.commit()
            
            return {"success": True, "project": project.to_dict()}
        finally:
            db.close()

    # --- GET PREVIEW ---
    @router.get("/projects/{project_id}/preview")
    async def get_project_preview(project_id: int, request: Request, timestamp: float = 0.0):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(VideoProject).filter(VideoProject.id == project_id)
            if user is not None:
                query = query.filter(VideoProject.owner == user)
            project = query.first()
            if not project:
                raise HTTPException(404, "Project not found")
                
            pub_settings = project.publish_settings or {}
            edit_instructions = pub_settings.get("edit_instructions", [])
            
            video_service = VideoService()
            preview_filename = f"preview_{project_id}.jpg"
            preview_path = os.path.abspath(os.path.join(video_service.render_dir, preview_filename))
            
            try:
                await video_service.render_preview(project_id, edit_instructions, timestamp, preview_path)
                return FileResponse(preview_path, media_type="image/jpeg")
            except Exception as e:
                logger.error(f"Preview rendering failed: {e}")
                raise HTTPException(500, f"Preview failed: {str(e)}")
        finally:
            db.close()

    # --- START RENDER ---
    @router.post("/projects/{project_id}/render")
    def start_project_render(project_id: int, request: Request, background_tasks: BackgroundTasks, body: Optional[ProjectEditRequest] = None):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(VideoProject).filter(VideoProject.id == project_id)
            if user is not None:
                query = query.filter(VideoProject.owner == user)
            project = query.first()
            if not project:
                raise HTTPException(404, "Project not found")
                
            edit_instructions = []
            quality = "high"
            if body:
                edit_instructions = body.edit_instructions
                quality = body.quality or "high"
            else:
                pub_settings = project.publish_settings or {}
                edit_instructions = pub_settings.get("edit_instructions", [])
                
            project.status = "rendering"
            db.commit()
            
            # Initialize progress status file
            video_service = VideoService()
            status_file = os.path.join(video_service.render_dir, f"status_{project_id}.json")
            try:
                os.makedirs(os.path.dirname(status_file), exist_ok=True)
                with open(status_file, "w") as f:
                    json.dump({"percent": 0, "message": "Queueing render job..."}, f)
            except Exception:
                pass
            
            background_tasks.add_task(run_project_render_background, project_id, edit_instructions, quality, user)
            
            return {"success": True, "message": "Render task started.", "project": project.to_dict()}
        finally:
            db.close()

    # --- GET RENDER STATUS ---
    @router.get("/projects/{project_id}/render/status")
    def get_render_status(project_id: int, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(VideoProject).filter(VideoProject.id == project_id)
            if user is not None:
                query = query.filter(VideoProject.owner == user)
            project = query.first()
            if not project:
                raise HTTPException(404, "Project not found")
                
            percent = 0
            message = ""
            video_service = VideoService()
            status_file = os.path.join(video_service.render_dir, f"status_{project_id}.json")
            if os.path.exists(status_file):
                try:
                    with open(status_file, "r") as f:
                        data = json.load(f)
                        percent = data.get("percent", 0)
                        message = data.get("message", "")
                except Exception:
                    pass
            elif project.status == "rendering":
                message = "Initializing render..."
            elif project.status == "ready":
                percent = 100
                message = "Rendering completed successfully!"
            elif project.status == "failed":
                message = "Render failed."
                
            return {
                "status": project.status,
                "output_path": project.output_path,
                "percent": percent,
                "message": message
            }
        finally:
            db.close()

    # --- DOWNLOAD RENDERED VIDEO ---
    @router.get("/projects/{project_id}/download")
    def download_rendered_video(project_id: int, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(VideoProject).filter(VideoProject.id == project_id)
            if user is not None:
                query = query.filter(VideoProject.owner == user)
            project = query.first()
            if not project:
                raise HTTPException(404, "Project not found")
                
            if not project.output_path or not os.path.exists(project.output_path):
                raise HTTPException(400, "Rendered video file not found or render has not completed yet.")
                
            return FileResponse(project.output_path, media_type="video/mp4", filename=f"project_{project_id}.mp4")
        finally:
            db.close()

    # --- YOUTUBE ACCOUNTS: LIST ---
    @router.get("/youtube/accounts")
    def list_youtube_accounts(request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(YouTubeAccount)
            if user is not None:
                query = query.filter(YouTubeAccount.owner == user)
            accounts = query.all()
            return [acc.to_dict() for acc in accounts]
        finally:
            db.close()

    # --- YOUTUBE ACCOUNTS: CREATE (START OAUTH) ---
    @router.post("/youtube/accounts")
    async def create_youtube_account(request: Request, body: YouTubeAccountCreate):
        user = _owner(request)
        db = SessionLocal()
        try:
            account = YouTubeAccount(
                owner=user,
                client_id=body.client_id,
                client_secret=body.client_secret,
                is_enabled=False
            )
            db.add(account)
            db.commit()
            db.refresh(account)
            
            redirect_uri = get_redirect_uri(request)
            
            from services.youtube_service import YouTubeService
            yt_service = YouTubeService()
            try:
                auth_url = await yt_service.start_oauth_flow(
                    client_id=body.client_id,
                    client_secret=body.client_secret,
                    redirect_uri=redirect_uri,
                    state=str(account.id)
                )
                return {"success": True, "auth_url": auth_url, "account": account.to_dict()}
            except Exception as e:
                logger.error(f"Failed to start OAuth flow: {e}")
                db.delete(account)
                db.commit()
                raise HTTPException(500, f"Failed to start OAuth flow: {str(e)}")
        finally:
            db.close()

    # --- YOUTUBE ACCOUNTS: CALLBACK ---
    @router.get("/youtube/oauth/callback", response_class=HTMLResponse)
    async def oauth_callback(request: Request, code: str = None, error: str = None, state: str = None):
        if error:
            return get_error_html(f"OAuth error: {error}")
        if not code or not state:
            return get_error_html("Missing code or state parameter.")
            
        db = SessionLocal()
        try:
            account_id = int(state)
            account = db.query(YouTubeAccount).filter(YouTubeAccount.id == account_id).first()
            if not account:
                return get_error_html(f"YouTube account record with ID {account_id} not found.")
                
            redirect_uri = get_redirect_uri(request)
            
            from services.youtube_service import YouTubeService
            yt_service = YouTubeService()
            
            try:
                await yt_service.complete_oauth(code, account, redirect_uri, db)
                return get_success_html()
            except Exception as e:
                logger.error(f"Failed to complete OAuth flow: {e}")
                db.delete(account)
                db.commit()
                return get_error_html(str(e))
        except ValueError:
            return get_error_html("Invalid state parameter format.")
        finally:
            db.close()

    # --- YOUTUBE ACCOUNTS: DELETE ---
    @router.delete("/youtube/accounts/{account_id}")
    def delete_youtube_account(account_id: int, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(YouTubeAccount).filter(YouTubeAccount.id == account_id)
            if user is not None:
                query = query.filter(YouTubeAccount.owner == user)
            account = query.first()
            if not account:
                raise HTTPException(404, "YouTube account not found")
            db.delete(account)
            db.commit()
            return {"success": True, "message": "YouTube account removed successfully."}
        finally:
            db.close()

    # --- YOUTUBE ACCOUNTS: TEST ---
    @router.post("/youtube/accounts/{account_id}/test")
    async def test_youtube_account(account_id: int, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(YouTubeAccount).filter(YouTubeAccount.id == account_id)
            if user is not None:
                query = query.filter(YouTubeAccount.owner == user)
            account = query.first()
            if not account:
                raise HTTPException(404, "YouTube account not found")
                
            from services.youtube_service import YouTubeService
            yt_service = YouTubeService()
            try:
                channels = await yt_service.list_channels(account, db)
                return {"success": True, "channels": channels}
            except Exception as e:
                logger.error(f"Connection test failed: {e}")
                raise HTTPException(500, f"Connection test failed: {str(e)}")
        finally:
            db.close()

    # --- PUBLISH PROJECT TO YOUTUBE ---
    @router.post("/projects/{project_id}/publish")
    def publish_project(project_id: int, request: Request, background_tasks: BackgroundTasks, body: VideoPublishRequest):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(VideoProject).filter(VideoProject.id == project_id)
            if user is not None:
                query = query.filter(VideoProject.owner == user)
            project = query.first()
            if not project:
                raise HTTPException(404, "Project not found")
                
            acc_query = db.query(YouTubeAccount).filter(YouTubeAccount.id == body.account_id)
            if user is not None:
                acc_query = acc_query.filter(YouTubeAccount.owner == user)
            account = acc_query.first()
            if not account:
                raise HTTPException(404, "YouTube account not found")
                
            if not project.output_path or not os.path.exists(project.output_path):
                raise HTTPException(400, "Project video must be rendered before publishing.")
                
            metadata = {
                "title": body.title or project.title or "Odysseus Video Studio upload",
                "description": body.description or project.description or "",
                "tags": body.tags or [],
                "privacy_status": body.privacy,
                "category_id": body.category,
                "thumbnail_path": body.thumbnail_path
            }
            
            project.status = "publishing"
            pub_settings = project.publish_settings or {}
            pub_settings["account_id"] = body.account_id
            pub_settings["metadata"] = metadata
            project.publish_settings = pub_settings
            db.commit()
            
            background_tasks.add_task(run_project_publish_background, project_id, body.account_id, metadata, user)
            
            return {"success": True, "message": "Publishing task started.", "project": project.to_dict()}
        finally:
            db.close()

    # --- PUBLISH STATUS ---
    @router.get("/projects/{project_id}/publish/status")
    async def get_publish_status(project_id: int, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            query = db.query(VideoProject).filter(VideoProject.id == project_id)
            if user is not None:
                query = query.filter(VideoProject.owner == user)
            project = query.first()
            if not project:
                raise HTTPException(404, "Project not found")
                
            if not project.youtube_video_id:
                return {
                    "success": True,
                    "published": False,
                    "status": project.status,
                    "youtube_status": None,
                    "video_id": None
                }
                
            pub_settings = project.publish_settings or {}
            account_id = pub_settings.get("account_id")
            if not account_id:
                return {
                    "success": True,
                    "published": True,
                    "status": project.status,
                    "youtube_status": "unknown (no account info)",
                    "video_id": project.youtube_video_id
                }
                
            account = db.query(YouTubeAccount).filter(YouTubeAccount.id == account_id).first()
            if not account:
                return {
                    "success": True,
                    "published": True,
                    "status": project.status,
                    "youtube_status": "unknown (account deleted)",
                    "video_id": project.youtube_video_id
                }
                
            from services.youtube_service import YouTubeService
            yt_service = YouTubeService()
            try:
                status_info = await yt_service.get_video_status(account, db, project.youtube_video_id)
                return {
                    "success": True,
                    "published": True,
                    "status": project.status,
                    "youtube_status": status_info,
                    "video_id": project.youtube_video_id
                }
            except Exception as e:
                logger.error(f"Failed to check YouTube video status: {e}")
                return {
                    "success": True,
                    "published": True,
                    "status": project.status,
                    "youtube_status": f"error: {str(e)}",
                    "video_id": project.youtube_video_id
                }
        finally:
            db.close()

    return router

def get_redirect_uri(request: Request) -> str:
    url = str(request.url_for("oauth_callback"))
    proto = request.headers.get("x-forwarded-proto")
    if proto:
        url = url.replace("http://", f"{proto}://").replace("https://", f"{proto}://")
    return url

def get_success_html() -> HTMLResponse:
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>YouTube Authorization Successful</title>
        <style>
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background-color: #0f0f11;
                color: #f1f1f1;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
            }
            .container {
                text-align: center;
                background: rgba(255, 255, 255, 0.05);
                backdrop-filter: blur(10px);
                padding: 40px;
                border-radius: 12px;
                border: 1px solid rgba(255, 255, 255, 0.1);
                max-width: 400px;
            }
            h1 {
                color: #ff0000;
                margin-bottom: 20px;
            }
            p {
                font-size: 16px;
                line-height: 1.5;
                margin-bottom: 30px;
            }
            .btn {
                background-color: #ff0000;
                color: white;
                border: none;
                padding: 10px 20px;
                font-size: 16px;
                border-radius: 6px;
                cursor: pointer;
                text-decoration: none;
                display: inline-block;
            }
            .btn:hover {
                background-color: #cc0000;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎬 Authorization Successful!</h1>
            <p>Your YouTube account has been successfully linked to Odysseus. You can now close this tab and return to the application.</p>
            <button class="btn" onclick="window.close()">Close Window</button>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=200)

def get_error_html(error_message: str) -> HTMLResponse:
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>YouTube Authorization Failed</title>
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background-color: #0f0f11;
                color: #f1f1f1;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
            }}
            .container {{
                text-align: center;
                background: rgba(255, 255, 255, 0.05);
                backdrop-filter: blur(10px);
                padding: 40px;
                border-radius: 12px;
                border: 1px solid rgba(255, 255, 255, 0.1);
                max-width: 400px;
            }}
            h1 {{
                color: #ff4444;
                margin-bottom: 20px;
            }}
            p {{
                font-size: 16px;
                line-height: 1.5;
                margin-bottom: 30px;
            }}
            .btn {{
                background-color: #444;
                color: white;
                border: none;
                padding: 10px 20px;
                font-size: 16px;
                border-radius: 6px;
                cursor: pointer;
                text-decoration: none;
                display: inline-block;
            }}
            .btn:hover {{
                background-color: #555;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>❌ Authorization Failed</h1>
            <p>An error occurred during Google authorization:</p>
            <p style="color: #ff9999; font-family: monospace; font-size: 14px;">{error_message}</p>
            <button class="btn" onclick="window.close()">Close Window</button>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html, status_code=400)
