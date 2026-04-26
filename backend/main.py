"""
backend/main.py
───────────────
FastAPI server — orchestrates the full video pipeline.

Endpoints:
  POST /generate          → starts a job, returns job_id
  GET  /status/{job_id}   → Server-Sent Events stream (progress updates)
  GET  /download/{job_id} → download the final MP4
  POST /upload/{job_id}   → upload to YouTube, returns video URL
  GET  /health            → health check
"""

import asyncio
import os
import uuid
import json
import shutil
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

from pipeline.scene_splitter import split_story_into_scenes
from pipeline.tts import generate_all_voiceovers
from pipeline.image_gen import generate_all_images
from pipeline.music import fetch_background_music
from pipeline.assembler import assemble_video
from pipeline.youtube import upload_to_youtube, generate_video_metadata

# ─── Configuration ────────────────────────────────────────────
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "outputs"))
TEMP_DIR = Path(os.getenv("TEMP_DIR", "temp"))
OUTPUT_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)

# ─── App ──────────────────────────────────────────────────────
app = FastAPI(
    title="Faceless Video Generator API",
    version="1.0.0",
    description="Turn a text story into a YouTube-ready MP4 video.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Lock down in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── In-memory job store ──────────────────────────────────────
# { job_id: { "status": str, "progress": int, "error": str|None,
#             "video_path": str|None, "scenes": list } }
jobs: dict[str, dict] = {}


# ─── Request / Response models ────────────────────────────────

class GenerateRequest(BaseModel):
    story: str = Field(..., min_length=50, max_length=10_000)


class GenerateResponse(BaseModel):
    job_id: str
    message: str


class UploadRequest(BaseModel):
    privacy: str = "private"   # private | unlisted | public


# ─── Helper ──────────────────────────────────────────────────

def _set_progress(job_id: str, step: str, pct: int, error: str | None = None):
    if job_id in jobs:
        jobs[job_id]["status"] = step
        jobs[job_id]["progress"] = pct
        if error:
            jobs[job_id]["error"] = error


# ─── Core pipeline (runs in background) ───────────────────────

async def run_pipeline(job_id: str, story: str):
    job_temp = TEMP_DIR / job_id
    job_temp.mkdir(parents=True, exist_ok=True)
    output_path = str(OUTPUT_DIR / f"{job_id}.mp4")

    try:
        # ── Stage 1: Scene splitting ──────────────────────────
        _set_progress(job_id, "Splitting story into scenes", 5)
        scenes = await split_story_into_scenes(story)
        jobs[job_id]["scenes"] = scenes

        # ── Stage 2: Parallel asset generation ───────────────
        _set_progress(job_id, "Generating voiceover, visuals & music", 10)

        voice_task = generate_all_voiceovers(scenes, str(job_temp))
        image_task = generate_all_images(scenes, str(job_temp))
        music_task = fetch_background_music(scenes, str(job_temp / "music.mp3"))

        voice_paths, image_paths, music_path = await asyncio.gather(
            voice_task, image_task, music_task
        )

        # ── Stage 3: Video assembly ───────────────────────────
        def progress_cb(step, pct):
            _set_progress(job_id, step, 40 + int(pct * 0.55))

        await assemble_video(
            scenes=scenes,
            image_paths=image_paths,
            voice_paths=voice_paths,
            music_path=music_path,
            output_path=output_path,
            progress_callback=progress_cb,
        )

        jobs[job_id]["video_path"] = output_path
        _set_progress(job_id, "complete", 100)

    except Exception as exc:
        _set_progress(job_id, "error", 0, error=str(exc))
        raise

    finally:
        # Clean up temp files
        try:
            shutil.rmtree(job_temp, ignore_errors=True)
        except Exception:
            pass


# ─── Routes ──────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "queued",
        "progress": 0,
        "error": None,
        "video_path": None,
        "scenes": [],
        "story": req.story,
    }
    background_tasks.add_task(run_pipeline, job_id, req.story)
    return GenerateResponse(job_id=job_id, message="Pipeline started")


@app.get("/status/{job_id}")
async def status_stream(job_id: str):
    """Server-Sent Events endpoint — streams progress to frontend."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    async def event_generator() -> AsyncGenerator[str, None]:
        last_pct = -1
        while True:
            job = jobs.get(job_id, {})
            pct = job.get("progress", 0)
            step = job.get("status", "queued")
            error = job.get("error")

            if pct != last_pct or step in ("complete", "error"):
                data = json.dumps({
                    "step": step,
                    "progress": pct,
                    "error": error,
                })
                yield f"data: {data}\n\n"
                last_pct = pct

            if step in ("complete", "error"):
                break

            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/download/{job_id}")
async def download(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "complete":
        raise HTTPException(status_code=400, detail="Video not ready yet")

    video_path = job["video_path"]
    if not video_path or not Path(video_path).exists():
        raise HTTPException(status_code=404, detail="Video file not found")

    return FileResponse(
        path=video_path,
        media_type="video/mp4",
        filename="story_video.mp4",
    )


@app.post("/upload/{job_id}")
async def upload_youtube(job_id: str, req: UploadRequest):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "complete":
        raise HTTPException(status_code=400, detail="Video not ready yet")

    story = job.get("story", "")
    scenes = job.get("scenes", [])
    metadata = generate_video_metadata(story, scenes)

    result = upload_to_youtube(
        video_path=job["video_path"],
        title=metadata["title"],
        description=metadata["description"],
        tags=metadata["tags"],
        privacy=req.privacy,
    )

    return {
        "video_id": result["video_id"],
        "url": result["url"],
        "privacy": req.privacy,
    }
