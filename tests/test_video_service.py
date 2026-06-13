import os
import pytest
from unittest.mock import MagicMock, patch
from services.video_service import VideoService, parse_llm_json

def test_parse_llm_json():
    # Test reasoning tag stripping
    input_text = "<think>We need to return JSON format</think>```json\n{\"format\": \"short\", \"reasoning\": \"reasons\"}\n```"
    result = parse_llm_json(input_text)
    assert result == {"format": "short", "reasoning": "reasons"}

    # Test clean JSON format directly
    input_text = '{"format": "both"}'
    result = parse_llm_json(input_text)
    assert result == {"format": "both"}

    # Test fallback extraction with raw text around JSON
    input_text = "Here is the response:\n{\"format\": \"long\"}\nHope this helps."
    result = parse_llm_json(input_text)
    assert result == {"format": "long"}

    # Test complete failure
    input_text = "This is not json at all."
    result = parse_llm_json(input_text)
    assert result == {}


def test_video_service_init():
    service = VideoService(data_dir="data_test")
    assert os.path.exists(service.upload_dir)
    assert os.path.exists(service.keyframe_dir)
    assert os.path.exists(service.render_dir)
    
    # Clean up test directories
    try:
        os.rmdir(service.upload_dir)
        os.rmdir(service.keyframe_dir)
        os.rmdir(service.render_dir)
        # remove parent video dir
        os.rmdir(os.path.join("data_test", "video"))
        os.rmdir("data_test")
    except Exception:
        pass


@patch("services.video_service.WHISPER_AVAILABLE", False)
def test_video_service_no_whisper():
    service = VideoService(data_dir="data_test")
    with pytest.raises(ImportError):
        service.transcribe_audio_sync("dummy_path.wav")
    
    # Clean up test directories
    try:
        os.rmdir(service.upload_dir)
        os.rmdir(service.keyframe_dir)
        os.rmdir(service.render_dir)
        os.rmdir(os.path.join("data_test", "video"))
        os.rmdir("data_test")
    except Exception:
        pass


@patch("services.video_service.MOVIEPY_AVAILABLE", False)
def test_video_service_no_moviepy():
    service = VideoService(data_dir="data_test")
    with pytest.raises(ImportError):
        service.get_video_metadata_sync("dummy_path.mp4")
        
    with pytest.raises(ImportError):
        service.extract_audio_sync("dummy_path.mp4", "out.wav")
        
    # Clean up test directories
    try:
        os.rmdir(service.upload_dir)
        os.rmdir(service.keyframe_dir)
        os.rmdir(service.render_dir)
        os.rmdir(os.path.join("data_test", "video"))
        os.rmdir("data_test")
    except Exception:
        pass


@patch("services.video_service.OPENCV_AVAILABLE", False)
def test_video_service_no_opencv():
    service = VideoService(data_dir="data_test")
    with pytest.raises(ImportError):
        service.extract_keyframes_sync("dummy_path.mp4", "out_dir")
        
    # Clean up test directories
    try:
        os.rmdir(service.upload_dir)
        os.rmdir(service.keyframe_dir)
        os.rmdir(service.render_dir)
        os.rmdir(os.path.join("data_test", "video"))
        os.rmdir("data_test")
    except Exception:
        pass


@pytest.mark.asyncio
@patch("src.llm_core.llm_call_async")
@patch("src.endpoint_resolver.resolve_endpoint")
@patch("core.database.SessionLocal")
async def test_analyze_project(mock_session_local, mock_resolve, mock_llm_call):
    mock_resolve.return_value = ("http://localhost/api", "gpt-4o", {})
    mock_llm_call.return_value = '{"format_recommendation": "both", "format_reasoning": "Fits both horizontal and vertical formats.", "suggested_title": "Cool Video", "suggested_description": "Descr", "suggested_tags": ["tag1"], "highlights": "Key highlights"}'
    
    mock_db = MagicMock()
    mock_session_local.return_value = mock_db
    
    mock_project = MagicMock()
    mock_project.id = 1
    mock_project.title = "Test Project"
    mock_project.publish_settings = {}
    
    mock_clip = MagicMock()
    mock_clip.duration_seconds = 10.0
    mock_clip.transcript = "Hello world transcript."
    mock_clip.visual_summary = "A person explaining code."
    
    mock_db.query.return_value.filter.return_value.first.return_value = mock_project
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [mock_clip]
    
    service = VideoService(data_dir="data_test")
    res = await service.analyze_project(project_id=1)
    
    assert res["format_recommendation"] == "both"
    assert mock_project.format_recommendation == "both"
    assert mock_project.publish_settings["title"] == "Cool Video"
    
    try:
        os.rmdir(service.upload_dir)
        os.rmdir(service.keyframe_dir)
        os.rmdir(service.render_dir)
        os.rmdir(os.path.join("data_test", "video"))
        os.rmdir("data_test")
    except Exception:
        pass


@pytest.mark.asyncio
@patch("src.llm_core.llm_call_async")
@patch("src.endpoint_resolver.resolve_endpoint")
@patch("core.database.SessionLocal")
async def test_suggest_edit_plan(mock_session_local, mock_resolve, mock_llm_call):
    mock_resolve.return_value = ("http://localhost/api", "gpt-4o", {})
    mock_llm_call.return_value = '{"reasoning": "Plan", "edit_instructions": [{"action": "trim", "clip_id": 1, "start": 0.0, "end": 5.0}]}'
    
    mock_db = MagicMock()
    mock_session_local.return_value = mock_db
    
    mock_project = MagicMock()
    mock_project.id = 1
    mock_project.title = "Test Project"
    
    mock_clip = MagicMock()
    mock_clip.id = 1
    mock_clip.transcript = "Speech segment"
    mock_clip.transcript_segments = None
    
    mock_db.query.return_value.filter.return_value.first.return_value = mock_project
    mock_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [mock_clip]
    
    service = VideoService(data_dir="data_test")
    res = await service.suggest_edit_plan(project_id=1)
    
    assert res["reasoning"] == "Plan"
    assert len(res["edit_instructions"]) == 1
    assert res["edit_instructions"][0]["action"] == "trim"
    
    try:
        os.rmdir(service.upload_dir)
        os.rmdir(service.keyframe_dir)
        os.rmdir(service.render_dir)
        os.rmdir(os.path.join("data_test", "video"))
        os.rmdir("data_test")
    except Exception:
        pass
