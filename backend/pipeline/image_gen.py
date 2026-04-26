"""
pipeline/image_gen.py
─────────────────────
Generates one cinematic image per scene using DALL·E 3.
Falls back to Stability AI (SDXL) if a stability key is present.
Images are saved as PNG in the temp directory.
"""

import os
import asyncio
import httpx
import base64
from pathlib import Path
from openai import AsyncOpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
STABILITY_API_KEY = os.getenv("STABILITY_API_KEY", "")

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Prefix added to every image prompt for consistent cinematic style
STYLE_PREFIX = (
    "Cinematic 9:16 vertical frame, photorealistic, dramatic lighting, "
    "deep shadows, film grain, no text, no watermarks, no letters. "
)


async def generate_scene_image(prompt: str, output_path: str) -> str:
    """
    Generate a 1024×1792 (portrait) image for the scene.
    Returns path to the saved PNG.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    full_prompt = STYLE_PREFIX + prompt

    if STABILITY_API_KEY:
        return await _stability_image(full_prompt, output_path)
    else:
        return await _dalle_image(full_prompt, output_path)


async def _dalle_image(prompt: str, output_path: str) -> str:
    response = await openai_client.images.generate(
        model="dall-e-3",
        prompt=prompt,
        size="1024x1792",     # closest portrait ratio available
        quality="standard",
        n=1,
        response_format="b64_json",
    )
    image_data = base64.b64decode(response.data[0].b64_json)
    with open(output_path, "wb") as f:
        f.write(image_data)
    return output_path


async def _stability_image(prompt: str, output_path: str) -> str:
    """Stability AI SDXL via REST API."""
    url = "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image"
    headers = {
        "Authorization": f"Bearer {STABILITY_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "text_prompts": [
            {"text": prompt, "weight": 1.0},
            {"text": "text, letters, words, watermark, ugly, blurry", "weight": -1.0},
        ],
        "cfg_scale": 7,
        "height": 1344,
        "width": 768,
        "steps": 30,
        "samples": 1,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

    image_data = base64.b64decode(data["artifacts"][0]["base64"])
    with open(output_path, "wb") as f:
        f.write(image_data)
    return output_path


async def generate_all_images(scenes: list[dict], temp_dir: str) -> list[str]:
    """
    Generates images for all scenes in parallel (with semaphore to avoid rate limits).
    Returns list of PNG file paths.
    """
    semaphore = asyncio.Semaphore(3)  # max 3 concurrent image requests

    async def guarded(scene, i):
        async with semaphore:
            return await generate_scene_image(
                scene["image_prompt"],
                os.path.join(temp_dir, f"image_{i:02d}.png"),
            )

    tasks = [guarded(scene, i) for i, scene in enumerate(scenes)]
    return await asyncio.gather(*tasks)
