import os
import uuid
import logging
from typing import Optional, List, Dict, Any
import shutil

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, BackgroundTasks
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


router = APIRouter(prefix="/api/video", tags=["video"])

# ---------------------------------------------------------------------------
# Background Task Helpers
# ---------------------------------------------------------------------------

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
            clip.transcript = transcription_result.get("transcript", "")
            clip.transcript_segments = transcription_result.get("segments", [])
            clip.has_audio = True
            db.commit()
            logger.info(f"Successfully transcribed clip {clip_id}")
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
            visual_summary = await video_service.generate_visual_summary(keyframes, owner=owner)
            clip.visual_summary = visual_summary
            db.commit()
            logger.info(f"Successfully generated visual summary for clip {clip_id}")
        except Exception as e:
            logger.error(f"Failed to generate visual summary for clip {clip_id}: {e}")
            clip.visual_summary = f"Visual analysis failed: {str(e)}"
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
            
            # 4. Extract metadata asynchronously
            try:
                metadata = await video_service.get_video_metadata(destination_path)
                clip.duration_seconds = metadata.get("duration", 0.0)
                clip.resolution = f"{metadata.get('width', 0)}x{metadata.get('height', 0)}"
                clip.fps = metadata.get("fps", 0.0)
                clip.has_audio = metadata.get("has_audio", True)
                db.commit()
            except Exception as metadata_ex:
                logger.warning(f"Failed to extract metadata on upload for clip {clip.id}: {metadata_ex}")

            # 5. Automatically trigger transcription and visual description in the background
            background_tasks.add_task(run_transcription_background, clip.id, user)
            background_tasks.add_task(run_visual_analysis_background, clip.id, user)

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
            if "error" in result:
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
        video_service = VideoService()
        try:
            result = await video_service.suggest_edit_plan(project_id, owner=user)
            if "error" in result:
                raise HTTPException(500, result["error"])
            return result
        except ValueError as ex:
            raise HTTPException(404, str(ex))
        except Exception as e:
            logger.error(f"Edit plan error: {e}")
            raise HTTPException(500, str(e))

    return router
