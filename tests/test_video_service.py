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
