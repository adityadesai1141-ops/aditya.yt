"""
pipeline/assembler.py
─────────────────────
Assembles the final MP4 from:
  - Per-scene images  (PNG)
  - Per-scene audio   (MP3 voiceover)
  - Background music  (MP3)

Steps:
  1. For each scene: apply Ken Burns zoom/pan to image → silent video
  2. Add scene voiceover audio
  3. Burn in word-level subtitles via OpenAI Whisper
  4. Crossfade-concat all scene clips
  5. Mix background music at low volume under voice
  6. Export 1080×1920 MP4 (H.264 / AAC)
"""

import os
import json
import asyncio
import subprocess
import tempfile
from pathlib import Path

from openai import AsyncOpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
VIDEO_W = int(os.getenv("VIDEO_WIDTH", 1080))
VIDEO_H = int(os.getenv("VIDEO_HEIGHT", 1920))
FPS = int(os.getenv("VIDEO_FPS", 30))
MUSIC_VOL = float(os.getenv("MUSIC_VOLUME", 0.18))
TRANSITION = float(os.getenv("SCENE_TRANSITION_SECS", 0.5))

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

async def assemble_video(
    scenes: list[dict],
    image_paths: list[str],
    voice_paths: list[str],
    music_path: str,
    output_path: str,
    progress_callback=None,
) -> str:
    """
    Full pipeline from assets → final MP4.
    progress_callback(step: str, pct: int) is called at each stage.
    """

    def _cb(step, pct):
        if progress_callback:
            progress_callback(step, pct)

    temp_dir = Path(output_path).parent / "assembly_temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    _cb("Generating subtitles", 5)
    subtitle_files = await _generate_subtitles(scenes, voice_paths, temp_dir)

    _cb("Rendering scene clips", 15)
    scene_clips = []
    for i, (img, voice, subs) in enumerate(
        zip(image_paths, voice_paths, subtitle_files)
    ):
        clip_path = str(temp_dir / f"clip_{i:02d}.mp4")
        _render_scene_clip(img, voice, subs, clip_path, i)
        scene_clips.append(clip_path)
        _cb("Rendering scene clips", 15 + int(60 * (i + 1) / len(scene_clips)))

    _cb("Concatenating scenes", 75)
    raw_concat = str(temp_dir / "concat_raw.mp4")
    _concat_clips(scene_clips, raw_concat)

    _cb("Mixing background music", 85)
    final_mix = str(temp_dir / "mixed.mp4")
    _mix_music(raw_concat, music_path, final_mix)

    _cb("Exporting final video", 92)
    _export_final(final_mix, output_path)

    _cb("Done", 100)
    return output_path


# ─────────────────────────────────────────────────────────────
# Step 1 — Whisper subtitle generation
# ─────────────────────────────────────────────────────────────

async def _generate_subtitles(
    scenes: list[dict], voice_paths: list[str], temp_dir: Path
) -> list[str]:
    """Transcribe each voiceover with Whisper → SRT file path."""
    tasks = [
        _whisper_transcribe(vp, temp_dir / f"subs_{i:02d}.srt")
        for i, vp in enumerate(voice_paths)
    ]
    return await asyncio.gather(*tasks)


async def _whisper_transcribe(voice_path: str, srt_path: Path) -> str:
    try:
        with open(voice_path, "rb") as f:
            transcript = await openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="verbose_json",
                timestamp_granularities=["word"],
            )

        srt_content = _words_to_srt(transcript.words or [])
        srt_path.write_text(srt_content, encoding="utf-8")
    except Exception as e:
        print(f"⚠ Whisper failed for {voice_path}: {e} — using empty subtitles")
        srt_path.write_text("", encoding="utf-8")

    return str(srt_path)


def _words_to_srt(words: list) -> str:
    """Convert Whisper word timestamps to SRT with 3-word groups."""
    lines = []
    idx = 1
    chunk_size = 3

    for i in range(0, len(words), chunk_size):
        chunk = words[i: i + chunk_size]
        start = _fmt_time(chunk[0].start)
        end = _fmt_time(chunk[-1].end)
        text = " ".join(w.word.strip() for w in chunk).upper()
        lines.append(f"{idx}\n{start} --> {end}\n{text}\n")
        idx += 1

    return "\n".join(lines)


def _fmt_time(secs: float) -> str:
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = int(secs % 60)
    ms = int((secs % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ─────────────────────────────────────────────────────────────
# Step 2 — Render individual scene clip (Ken Burns + captions)
# ─────────────────────────────────────────────────────────────

def _get_audio_duration(path: str) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", path,
        ],
        capture_output=True, text=True,
    )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def _render_scene_clip(
    image_path: str,
    voice_path: str,
    srt_path: str,
    output_path: str,
    scene_index: int,
) -> None:
    duration = _get_audio_duration(voice_path)

    # Ken Burns: alternating zoom directions to prevent monotony
    zoom_dir = scene_index % 4
    if zoom_dir == 0:
        # Slow zoom in, pan right
        zoompan = (
            f"scale={VIDEO_W * 2}:{VIDEO_H * 2},"
            f"zoompan=z='min(zoom+0.0008,1.5)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={int(duration * FPS)}:s={VIDEO_W}x{VIDEO_H}:fps={FPS}"
        )
    elif zoom_dir == 1:
        # Zoom in from top-left
        zoompan = (
            f"scale={VIDEO_W * 2}:{VIDEO_H * 2},"
            f"zoompan=z='min(zoom+0.0006,1.4)':x='0':y='0':"
            f"d={int(duration * FPS)}:s={VIDEO_W}x{VIDEO_H}:fps={FPS}"
        )
    elif zoom_dir == 2:
        # Zoom out
        zoompan = (
            f"scale={VIDEO_W * 2}:{VIDEO_H * 2},"
            f"zoompan=z='if(lte(zoom,1.0),1.5,max(zoom-0.0008,1.0))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={int(duration * FPS)}:s={VIDEO_W}x{VIDEO_H}:fps={FPS}"
        )
    else:
        # Pan left
        zoompan = (
            f"scale={VIDEO_W * 2}:{VIDEO_H * 2},"
            f"zoompan=z='1.3':x='iw/2-(iw/zoom/2)+{VIDEO_W // 4}*t/{duration}':y='ih/2-(ih/zoom/2)':"
            f"d={int(duration * FPS)}:s={VIDEO_W}x{VIDEO_H}:fps={FPS}"
        )

    # Subtitle filter (bold, white, black outline — TikTok/Shorts style)
    has_subs = srt_path and Path(srt_path).stat().st_size > 10
    subtitle_filter = ""
    if has_subs:
        safe_srt = srt_path.replace("\\", "/").replace(":", "\\:")
        subtitle_filter = (
            f",subtitles={safe_srt}:force_style='"
            f"FontName=Arial,FontSize=22,Bold=1,"
            f"PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
            f"BorderStyle=1,Outline=3,Shadow=1,"
            f"Alignment=2,MarginV=80'"
        )

    vf = zoompan + subtitle_filter

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", image_path,
        "-i", voice_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "128k",
        "-t", str(duration),
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


# ─────────────────────────────────────────────────────────────
# Step 3 — Concat with crossfade
# ─────────────────────────────────────────────────────────────

def _concat_clips(clip_paths: list[str], output_path: str) -> None:
    """Concatenate clips with xfade video transition and acrossfade audio."""
    if len(clip_paths) == 1:
        import shutil
        shutil.copy(clip_paths[0], output_path)
        return

    # Get durations
    durations = [_get_audio_duration(p) for p in clip_paths]

    # Build complex filtergraph for xfade
    inputs = []
    for p in clip_paths:
        inputs += ["-i", p]

    # xfade chain
    vfilter_parts = []
    afilter_parts = []
    offset = 0.0

    prev_v = "[0:v]"
    prev_a = "[0:a]"

    for i in range(1, len(clip_paths)):
        offset += durations[i - 1] - TRANSITION
        out_v = f"[v{i}]" if i < len(clip_paths) - 1 else "[vout]"
        out_a = f"[a{i}]" if i < len(clip_paths) - 1 else "[aout]"

        vfilter_parts.append(
            f"{prev_v}[{i}:v]xfade=transition=fade:duration={TRANSITION}:offset={offset:.3f}{out_v}"
        )
        afilter_parts.append(
            f"{prev_a}[{i}:a]acrossfade=d={TRANSITION}{out_a}"
        )

        prev_v = out_v
        prev_a = out_a

    filter_complex = ";".join(vfilter_parts + afilter_parts)

    cmd = (
        ["ffmpeg", "-y"]
        + inputs
        + [
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-preset", "fast",
            "-c:a", "aac",
            "-b:a", "192k",
            output_path,
        ]
    )
    subprocess.run(cmd, check=True, capture_output=True)


# ─────────────────────────────────────────────────────────────
# Step 4 — Mix background music
# ─────────────────────────────────────────────────────────────

def _mix_music(video_path: str, music_path: str, output_path: str) -> None:
    video_duration = _get_audio_duration(video_path)

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-stream_loop", "-1",   # loop music if shorter than video
        "-i", music_path,
        "-filter_complex",
        (
            f"[1:a]volume={MUSIC_VOL},afade=t=out:st={video_duration - 3}:d=3[music];"
            f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        ),
        "-map", "0:v",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


# ─────────────────────────────────────────────────────────────
# Step 5 — Final export with metadata
# ─────────────────────────────────────────────────────────────

def _export_final(input_path: str, output_path: str) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",   # web-optimised — playback starts before full download
        "-metadata", "title=Faceless Story Video",
        "-metadata", "comment=Generated by Faceless Video Generator",
        output_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
