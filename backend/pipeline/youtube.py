"""
pipeline/youtube.py
───────────────────
Uploads the final MP4 to YouTube using the Data API v3.

Auth flow:
  1. First run: opens a browser for OAuth consent → saves token.json
  2. Subsequent runs: uses saved token (auto-refreshed)

Scopes required:
  - https://www.googleapis.com/auth/youtube.upload
"""

import os
import pickle
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRETS_FILE = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json")
TOKEN_FILE = "youtube_token.pickle"

CATEGORY_IDS = {
    "entertainment": "24",
    "education":     "27",
    "howto":         "26",
    "gaming":        "20",
    "news":          "25",
}


def _get_credentials():
    """Load or refresh OAuth2 credentials."""
    creds = None

    if Path(TOKEN_FILE).exists():
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRETS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    return creds


def upload_to_youtube(
    video_path: str,
    title: str,
    description: str,
    tags: list[str] | None = None,
    category: str = "entertainment",
    privacy: str = "private",       # private | unlisted | public
) -> dict:
    """
    Upload video to YouTube.
    Returns dict with 'video_id' and 'url'.

    privacy="private" is the safe default — user can publish manually.
    """
    creds = _get_credentials()
    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title[:100],       # YouTube title limit
            "description": description[:5000],
            "tags": tags or [],
            "categoryId": CATEGORY_IDS.get(category, "24"),
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 5,  # 5 MB chunks
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    print("📤 Uploading to YouTube...")
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            print(f"   Upload progress: {pct}%")

    video_id = response["id"]
    url = f"https://www.youtube.com/watch?v={video_id}"
    print(f"✅ Upload complete: {url}")

    return {"video_id": video_id, "url": url}


def generate_video_metadata(story: str, scenes: list[dict]) -> dict:
    """
    Generate a YouTube title, description, and tags from the story.
    Uses the first scene narration as the hook.
    """
    # Simple heuristic — first 60 chars of story as title base
    title_base = story[:60].rsplit(" ", 1)[0] + "..."
    title = f"🔴 {title_base}"

    description_lines = [
        "A cinematic AI-generated story.",
        "",
        "— STORY —",
        story[:800] + ("..." if len(story) > 800 else ""),
        "",
        "#shorts #story #aistory #faceless #youtube",
    ]
    description = "\n".join(description_lines)

    tags = [
        "story", "ai story", "faceless", "reddit story",
        "scary story", "true story", "shorts", "youtube shorts",
    ]

    return {"title": title, "description": description, "tags": tags}
