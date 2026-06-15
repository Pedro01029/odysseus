import asyncio
import os
import sys
import json
import httpx
import re

# Add project root to path
sys.path.append(os.path.abspath("."))

from core.database import SessionLocal, VideoProject, VideoClip
from src.endpoint_resolver import resolve_endpoint

def clean_transcript_text(text: str) -> str:
    if not text:
        return ""
    # 1. Replace 3 or more repeating words separated by spaces (case-insensitive)
    text = re.sub(r"\b(\w+)(?:\s+\1){2,}\b", r"\1", text, flags=re.IGNORECASE)
    
    # 2. Replace 3 or more repeating periods separated by spaces or not
    text = re.sub(r"\.(?:\s*\.){2,}", ".", text)
    
    # 3. Clean up extra whitespaces
    text = re.sub(r"\s+", " ", text).strip()
    return text

async def main():
    db = SessionLocal()
    try:
        project = db.query(VideoProject).order_by(VideoProject.id.desc()).first()
        if not project:
            print("No project found.")
            return
            
        clips = db.query(VideoClip).filter(VideoClip.project_id == project.id).all()
        total_duration = sum(c.duration_seconds or 0.0 for c in clips)
        aggregated_transcript = []
        aggregated_visuals = []
        
        for i, c in enumerate(clips):
            c_name = c.file_name or f"Clip {i+1}"
            if c.transcript:
                cleaned = clean_transcript_text(c.transcript)
                print(f"Original transcript length: {len(c.transcript)}, Cleaned length: {len(cleaned)}")
                print(f"Cleaned snippet: {cleaned[:300]}")
                aggregated_transcript.append(f"[{c_name}] (duration: {c.duration_seconds or 0.0:.1f}s):\n{cleaned}")
            if c.visual_summary and "unavailable" not in c.visual_summary:
                aggregated_visuals.append(f"[{c_name}]:\n{c.visual_summary}")
                
        transcript_text = "\n\n".join(aggregated_transcript)
        visuals_text = "\n\n".join(aggregated_visuals)
        
        url, model, headers = resolve_endpoint("utility", owner=None)
        if not url or not model:
            url, model, headers = resolve_endpoint("default", owner=None)
            
        prompt = (
            "You are an expert AI video content strategist and editor.\n"
            "Analyze the following aggregated details from the source clips of a video project to recommend publishing options and content highlights.\n\n"
            f"Project Title: {project.title}\n"
            f"Total Duration: {total_duration:.1f} seconds\n"
            f"Aggregated Clips Transcripts:\n{transcript_text}\n\n"
            f"Aggregated Clips Visual Summaries:\n{visuals_text}\n\n"
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
        
        print("\nSending cleaned prompt to LLM...")
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3
        }
        
        r = httpx.post(url, json=payload, timeout=60.0)
        print(f"Status: {r.status_code}")
        data = r.json()
        
        print("\n--- SERVER RESPONSE MESSAGE OBJECT ---")
        message_obj = data["choices"][0]["message"]
        print(json.dumps(message_obj, indent=2))
        
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(main())
