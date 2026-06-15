import os
import logging
import asyncio
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional Google API Imports
# ---------------------------------------------------------------------------
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    GOOGLE_API_AVAILABLE = True
except ImportError:
    GOOGLE_API_AVAILABLE = False
    logger.warning("Google API client libraries not fully installed. YouTube functionality will be limited.")

class YouTubeService:
    def __init__(self):
        if not GOOGLE_API_AVAILABLE:
            logger.warning("YouTubeService initialized but Google API libraries are not available.")

    def is_available(self) -> bool:
        return GOOGLE_API_AVAILABLE

    async def start_oauth_flow(self, client_id: str, client_secret: str, redirect_uri: str, state: str) -> str:
        """
        Generate the Google OAuth authorization URL.
        Returns the authorization URL.
        """
        if not GOOGLE_API_AVAILABLE:
            raise ImportError("Google API client libraries are not installed.")

        client_config = {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }

        flow = Flow.from_client_config(
            client_config,
            scopes=[
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly"
            ],
            redirect_uri=redirect_uri
        )

        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=state
        )
        return auth_url

    async def complete_oauth(self, code: str, account: Any, redirect_uri: str, db) -> Dict[str, Any]:
        """
        Exchange auth code for access/refresh tokens.
        Updates the account in the database and fetches its channel info.
        """
        if not GOOGLE_API_AVAILABLE:
            raise ImportError("Google API client libraries are not installed.")

        client_config = {
            "web": {
                "client_id": account.client_id,
                "client_secret": account.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }

        flow = Flow.from_client_config(
            client_config,
            scopes=[
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly"
            ],
            redirect_uri=redirect_uri
        )

        # Execute blocking fetch_token in thread
        await asyncio.to_thread(flow.fetch_token, code=code)
        creds = flow.credentials

        account.access_token = creds.token
        if creds.refresh_token:
            account.refresh_token = creds.refresh_token
        account.token_expiry = creds.expiry
        account.is_enabled = True

        # Fetch channel details
        youtube = build("youtube", "v3", credentials=creds)
        channels_response = await asyncio.to_thread(
            youtube.channels().list(part="snippet", mine=True).execute
        )

        if channels_response.get("items"):
            channel = channels_response["items"][0]
            account.channel_id = channel["id"]
            account.channel_name = channel["snippet"]["title"]
        else:
            raise Exception("No YouTube channel found for the authenticated Google account.")

        db.commit()
        return account.to_dict()

    async def get_youtube_client(self, account: Any, db) -> Any:
        """
        Get an authenticated YouTube service client, refreshing the token if expired.
        """
        if not GOOGLE_API_AVAILABLE:
            raise ImportError("Google API client libraries are not installed.")

        creds = Credentials(
            token=account.access_token,
            refresh_token=account.refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=account.client_id,
            client_secret=account.client_secret
        )

        if not creds.valid:
            logger.info(f"Refreshing YouTube access token for account {account.id}")
            # Execute blocking refresh in thread
            await asyncio.to_thread(creds.refresh, Request())
            account.access_token = creds.token
            account.token_expiry = creds.expiry
            db.commit()

        return build("youtube", "v3", credentials=creds)

    async def list_channels(self, account: Any, db) -> List[Dict[str, Any]]:
        """
        List channels accessible by the authenticated account.
        """
        youtube = await self.get_youtube_client(account, db)
        response = await asyncio.to_thread(
            youtube.channels().list(part="snippet,id", mine=True).execute
        )
        channels = []
        for item in response.get("items", []):
            channels.append({
                "id": item["id"],
                "title": item["snippet"]["title"],
                "thumbnail": item["snippet"].get("thumbnails", {}).get("default", {}).get("url")
            })
        return channels

    async def get_video_status(self, account: Any, db, video_id: str) -> Dict[str, Any]:
        """
        Check the uploading and processing status of a video on YouTube.
        """
        youtube = await self.get_youtube_client(account, db)
        response = await asyncio.to_thread(
            youtube.videos().list(part="status,processingDetails", id=video_id).execute
        )
        items = response.get("items", [])
        if not items:
            return {"status": "not_found", "processing_status": None, "privacy": None}

        video = items[0]
        status_info = video.get("status", {})
        proc_info = video.get("processingDetails", {})

        return {
            "status": status_info.get("uploadStatus", "unknown"),  # e.g., uploaded, processed, failed, rejected
            "processing_status": proc_info.get("processingStatus", "unknown"),  # e.g., processing, succeeded, failed
            "privacy": status_info.get("privacyStatus"),
            "embeddable": status_info.get("embeddable"),
            "rejection_reason": status_info.get("rejectionReason"),
            "failure_reason": status_info.get("failureReason"),
        }

    async def upload_video(self, account: Any, db, video_path: str, metadata: Dict[str, Any]) -> str:
        """
        Upload video via YouTube Data API v3 resumable upload.
        metadata: {title, description, tags, category_id, privacy_status, thumbnail_path}
        Returns the YouTube video ID.
        """
        youtube = await self.get_youtube_client(account, db)

        body = {
            "snippet": {
                "title": metadata.get("title", "My Video")[:100],  # YouTube title limit is 100 chars
                "description": metadata.get("description", ""),
                "tags": metadata.get("tags", []),
                "categoryId": metadata.get("category_id", "22")  # Default to 22 (People & Blogs)
            },
            "status": {
                "privacyStatus": metadata.get("privacy_status", "private"),  # public, private, unlisted
                "selfDeclaredMadeForKids": False
            }
        }

        media = MediaFileUpload(
            video_path,
            chunksize=1024 * 1024,  # 1MB chunks
            resumable=True
        )

        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        def _execute_upload():
            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    logger.info(f"YouTube upload progress: {int(status.progress() * 100)}%")
            return response

        response_data = await asyncio.to_thread(_execute_upload)
        video_id = response_data.get("id")

        if not video_id:
            raise Exception("Failed to upload video: No ID returned from YouTube API.")

        # Upload thumbnail if path is provided and exists
        thumbnail_path = metadata.get("thumbnail_path")
        if thumbnail_path and os.path.exists(thumbnail_path):
            try:
                def _upload_thumbnail():
                    youtube.thumbnails().set(
                        videoId=video_id,
                        media_body=MediaFileUpload(thumbnail_path)
                    ).execute()
                await asyncio.to_thread(_upload_thumbnail)
                logger.info(f"Successfully uploaded thumbnail for YouTube video {video_id}")
            except Exception as e:
                logger.error(f"Failed to upload thumbnail for YouTube video {video_id}: {e}")

        return video_id
