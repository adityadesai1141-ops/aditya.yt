"""
pipeline/tts.py
───────────────
Converts narration text → MP3 audio file.
Primary  : ElevenLabs (more natural, emotional)
Fallback : OpenAI TTS (tts-1-hd, "onyx" voice)
"""

import os
import asyncio
from pathlib import Path
import httpx
from openai import AsyncOpenAI

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


async def generate_voiceover(text: str, output_path: str) -> str:
    """
    Generate a voiceover MP3 for `text` and save to `output_path`.
    Returns the output_path on success.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if ELEVENLABS_API_KEY:
        return await _elevenlabs_tts(text, output_path)
    else:
        return await _openai_tts(text, output_path)


async def _elevenlabs_tts(text: str, output_path: str) -> str:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.45,
            "similarity_boost": 0.80,
            "style": 0.35,
            "use_speaker_boost": True,
        },
    }

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()

    with open(output_path, "wb") as f:
        f.write(response.content)

    return output_path


async def _openai_tts(text: str, output_path: str) -> str:
    response = await openai_client.audio.speech.create(
        model="tts-1-hd",
        voice="onyx",          # deep, calm narrator voice
        input=text,
        speed=0.95,            # slightly slower for drama
    )
    response.stream_to_file(output_path)
    return output_path


async def generate_all_voiceovers(
    scenes: list[dict], temp_dir: str
) -> list[str]:
    """
    Generates voiceovers for all scenes in parallel.
    Returns list of MP3 file paths (same order as scenes).
    """
    tasks = [
        generate_voiceover(
            scene["narration"],
            os.path.join(temp_dir, f"voice_{i:02d}.mp3"),
        )
        for i, scene in enumerate(scenes)
    ]
    return await asyncio.gather(*tasks)
