import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from services.youtube_service import YouTubeService
from src.tool_implementations import do_publish_youtube
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routes.video_routes import setup_video_routes

# Create a test FastAPI application and mount the video routes
app = FastAPI()
app.include_router(setup_video_routes())
client = TestClient(app)

@pytest.mark.asyncio
@patch("services.youtube_service.GOOGLE_API_AVAILABLE", True)
@patch("services.youtube_service.Flow", create=True)
async def test_start_oauth_flow(mock_flow):
    mock_flow_instance = MagicMock()
    mock_flow_instance.authorization_url.return_value = ("https://accounts.google.com/o/oauth2/auth?state=xyz", "xyz")
    mock_flow.from_client_config.return_value = mock_flow_instance

    service = YouTubeService()
    url = await service.start_oauth_flow(
        client_id="client123",
        client_secret="secret123",
        redirect_uri="http://localhost/callback",
        state="1"
    )
    assert "https://accounts.google.com/o/oauth2/auth" in url
    mock_flow.from_client_config.assert_called_once()
    mock_flow_instance.authorization_url.assert_called_once_with(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state="1"
    )

@pytest.mark.asyncio
@patch("services.youtube_service.GOOGLE_API_AVAILABLE", True)
@patch("services.youtube_service.Flow", create=True)
@patch("services.youtube_service.build", create=True)
async def test_complete_oauth(mock_build, mock_flow):
    mock_flow_instance = MagicMock()
    mock_flow_instance.credentials = MagicMock(
        token="access_123",
        refresh_token="refresh_123",
        expiry="2026-06-13T23:59:59"
    )
    mock_flow.from_client_config.return_value = mock_flow_instance

    # Mock channels().list().execute()
    mock_youtube = MagicMock()
    mock_youtube.channels().list().execute.return_value = {
        "items": [
            {
                "id": "channel_id_123",
                "snippet": {"title": "Test Channel"}
            }
        ]
    }
    mock_build.return_value = mock_youtube

    # Mock database account row
    mock_account = MagicMock()
    mock_account.client_id = "client123"
    mock_account.client_secret = "secret123"
    
    mock_db = MagicMock()

    service = YouTubeService()
    await service.complete_oauth(
        code="auth_code_123",
        account=mock_account,
        redirect_uri="http://localhost/callback",
        db=mock_db
    )

    assert mock_account.access_token == "access_123"
    assert mock_account.refresh_token == "refresh_123"
    assert mock_account.channel_id == "channel_id_123"
    assert mock_account.channel_name == "Test Channel"
    assert mock_account.is_enabled is True
    mock_db.commit.assert_called_once()

@pytest.mark.asyncio
@patch("services.youtube_service.GOOGLE_API_AVAILABLE", True)
@patch("services.youtube_service.Credentials", create=True)
@patch("services.youtube_service.build", create=True)
async def test_list_channels(mock_build, mock_creds):
    mock_youtube = MagicMock()
    mock_youtube.channels().list().execute.return_value = {
        "items": [
            {
                "id": "ch1",
                "snippet": {
                    "title": "Ch1 Title",
                    "thumbnails": {"default": {"url": "http://image.jpg"}}
                }
            }
        ]
    }
    mock_build.return_value = mock_youtube

    mock_account = MagicMock()
    mock_account.access_token = "tok"
    mock_account.refresh_token = "ref"
    mock_account.client_id = "cid"
    mock_account.client_secret = "sec"
    
    mock_db = MagicMock()

    service = YouTubeService()
    channels = await service.list_channels(mock_account, mock_db)
    assert len(channels) == 1
    assert channels[0]["id"] == "ch1"
    assert channels[0]["title"] == "Ch1 Title"

@pytest.mark.asyncio
@patch("services.youtube_service.GOOGLE_API_AVAILABLE", True)
@patch("services.youtube_service.Credentials", create=True)
@patch("services.youtube_service.build", create=True)
async def test_get_video_status(mock_build, mock_creds):
    mock_youtube = MagicMock()
    mock_youtube.videos().list().execute.return_value = {
        "items": [
            {
                "status": {
                    "uploadStatus": "processed",
                    "privacyStatus": "public",
                    "embeddable": True
                },
                "processingDetails": {
                    "processingStatus": "succeeded"
                }
            }
        ]
    }
    mock_build.return_value = mock_youtube

    mock_account = MagicMock()
    service = YouTubeService()
    status = await service.get_video_status(mock_account, MagicMock(), "vid123")
    assert status["status"] == "processed"
    assert status["processing_status"] == "succeeded"
    assert status["privacy"] == "public"

@pytest.mark.asyncio
@patch("services.youtube_service.GOOGLE_API_AVAILABLE", True)
@patch("services.youtube_service.Credentials", create=True)
@patch("services.youtube_service.build", create=True)
@patch("services.youtube_service.MediaFileUpload", create=True)
@patch("os.path.exists", return_value=True)
async def test_upload_video(mock_exists, mock_media, mock_build, mock_creds):
    mock_youtube = MagicMock()
    
    # Mock video insert resumable chunks execution
    mock_request = MagicMock()
    mock_request.next_chunk.side_effect = [
        (MagicMock(progress=lambda: 0.5), None),
        (MagicMock(progress=lambda: 1.0), {"id": "new_video_id"})
    ]
    mock_youtube.videos().insert.return_value = mock_request
    mock_build.return_value = mock_youtube

    mock_account = MagicMock()
    service = YouTubeService()
    metadata = {
        "title": "Uploaded Title",
        "description": "Uploaded Descr",
        "tags": ["tag1"],
        "privacy_status": "unlisted",
        "thumbnail_path": "thumbnail.jpg"
    }
    
    # Mock thumbnail upload
    mock_youtube.thumbnails().set.return_value.execute.return_value = {}

    video_id = await service.upload_video(mock_account, MagicMock(), "dummy.mp4", metadata)
    assert video_id == "new_video_id"
    mock_youtube.videos().insert.assert_called_once()
    mock_youtube.thumbnails().set.assert_called_once()

@pytest.mark.asyncio
@patch("core.database.SessionLocal")
@patch("services.youtube_service.YouTubeService.upload_video", new_callable=AsyncMock)
@patch("services.youtube_service.YouTubeService.is_available", return_value=True)
@patch("os.path.exists", return_value=True)
async def test_do_publish_youtube_tool(mock_exists, mock_is_available, mock_upload, mock_session_local):
    mock_db = MagicMock()
    mock_session_local.return_value = mock_db

    mock_project = MagicMock()
    mock_project.id = 1
    mock_project.output_path = "render.mp4"
    mock_project.publish_settings = {}
    mock_db.query.return_value.filter.return_value.first.side_effect = [mock_project, MagicMock(id=5, is_enabled=True)]

    mock_upload.return_value = "video_xyz_123"

    tool_input = '{"project_id": 1, "account_id": 5, "title": "Tool Title", "description": "Tool Desc", "tags": ["t1"], "privacy": "public"}'
    result = await do_publish_youtube(tool_input)

    assert result["exit_code"] == 0
    assert "video_xyz_123" in result["response"]
    assert mock_project.status == "published"
    assert mock_project.youtube_video_id == "video_xyz_123"

@patch("routes.video_routes.get_current_user", return_value="test_user")
@patch("routes.video_routes.SessionLocal")
def test_youtube_api_endpoints_list(mock_session_local, mock_auth):
    mock_db = MagicMock()
    mock_session_local.return_value = mock_db
    
    mock_account = MagicMock()
    mock_account.to_dict.return_value = {"id": 1, "channel_name": "My Channel"}
    mock_db.query.return_value.filter.return_value.all.return_value = [mock_account]

    response = client.get("/api/video/youtube/accounts")
    assert response.status_code == 200
    assert response.json() == [{"id": 1, "channel_name": "My Channel"}]

@patch("routes.video_routes.get_current_user", return_value="test_user")
@patch("routes.video_routes.SessionLocal")
@patch("services.youtube_service.YouTubeService.start_oauth_flow", new_callable=AsyncMock)
def test_youtube_api_endpoints_create(mock_oauth_flow, mock_session_local, mock_auth):
    mock_db = MagicMock()
    mock_session_local.return_value = mock_db
    
    mock_oauth_flow.return_value = "https://auth-url.com"

    response = client.post(
        "/api/video/youtube/accounts",
        json={"client_id": "cid", "client_secret": "csec"}
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["auth_url"] == "https://auth-url.com"
    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()
