"""
pipeline/music.py
─────────────────
Fetches royalty-free background music from Freesound.org
matched to the dominant mood of the video.

If FREESOUND_API_KEY is not set, falls back to a local silence track
so the rest of the pipeline still works for testing.
"""

import os
import asyncio
import httpx
from pathlib import Path

FREESOUND_API_KEY = os.getenv("FREESOUND_API_KEY", "")

# Mood → search query mapping
MOOD_QUERIES: dict[str, str] = {
    "calm":       "ambient calm background music relaxing",
    "tense":      "suspense thriller tension dark background",
    "mysterious": "mysterious dark ambient ethereal background",
    "uplifting":  "uplifting cinematic hopeful background music",
    "dark":       "dark cinematic atmospheric horror background",
    "eerie":      "eerie unsettling ambient horror soundscape",
}

# Minimum duration we want (seconds) — pipeline will loop if shorter
MIN_DURATION_SECS = 60


def _dominant_mood(scenes: list[dict]) -> str:
    """Returns the most common mood across all scenes."""
    from collections import Counter
    moods = [s.get("mood", "calm") for s in scenes]
    return Counter(moods).most_common(1)[0][0]


async def fetch_background_music(
    scenes: list[dict], output_path: str
) -> str:
    """
    Download a suitable background track to output_path.
    Returns path to the downloaded file (MP3).
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if not FREESOUND_API_KEY:
        print("⚠  FREESOUND_API_KEY not set — using silence placeholder.")
        return await _create_silence(output_path, duration=180)

    mood = _dominant_mood(scenes)
    query = MOOD_QUERIES.get(mood, MOOD_QUERIES["calm"])

    sound_id, preview_url = await _search_freesound(query)
    if not preview_url:
        return await _create_silence(output_path, duration=180)

    await _download_file(preview_url, output_path)
    return output_path


async def _search_freesound(query: str) -> tuple[int | None, str | None]:
    """Search Freesound and return (sound_id, preview_hq_mp3_url)."""
    url = "https://freesound.org/apiv2/search/text/"
    params = {
        "query": query,
        "filter": f"duration:[{MIN_DURATION_SECS} TO 600] license:\"Creative Commons 0\"",
        "fields": "id,name,previews,duration",
        "page_size": 5,
        "token": FREESOUND_API_KEY,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])
    if not results:
        return None, None

    best = results[0]
    preview_url = best["previews"].get("preview-hq-mp3") or best["previews"].get("preview-lq-mp3")
    return best["id"], preview_url


async def _download_file(url: str, path: str) -> None:
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(path, "wb") as f:
                async for chunk in resp.aiter_bytes(8192):
                    f.write(chunk)


async def _create_silence(output_path: str, duration: int = 180) -> str:
    """Create a silent MP3 using ffmpeg as a placeholder."""
    import subprocess
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"anullsrc=r=44100:cl=stereo",
            "-t", str(duration),
            "-q:a", "9",
            "-acodec", "libmp3lame",
            output_path,
        ],
        capture_output=True,
        check=True,
    )
    return output_path
