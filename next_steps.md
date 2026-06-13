# Next Steps

The following steps are scheduled in the implementation plan:

1. **GitHub Branch Initialization**:
   - Create a new branch `feature/video-studio` from the current `mcp-turboquant` branch.
   - Push the new branch to `Pedro01029/odysseus`.

2. **Step 2 — Video & Audio Reading Capabilities**:
   - Update `requirements-optional.txt` with `faster-whisper`, `moviepy`, `opencv-python`, and `yt-dlp`.
   - Implement `VideoProject`, `VideoClip`, and `YouTubeAccount` database models in `core/database.py`.
   - Create `services/video_service.py` to extract audio, run `faster-whisper` transcription, generate keyframes, and parse metadata.
   - Create `/api/video` routes in `routes/video_routes.py` and register them in `app.py`.

3. **Step 3 — Content Analysis & Format Recommendation**:
   - Write formatting and edit plan prompting scripts.
   - Implement `parse_llm_json` helper to ensure robust model compatibility.

4. **Step 4 — Video Editing (moviepy)**:
   - Build programmatic editing functions for trimming, concatenation, subtitles overlay, and vertical crop/resizing.

5. **Step 5 — YouTube publishing and OAuth**:
   - Integrate Google OAuth flow and resumable video uploads.

6. **Step 6 — Frontend Panels**:
   - Code `static/js/video.js` and update `static/index.html` and `static/style.css` to add the Video Studio UI.
