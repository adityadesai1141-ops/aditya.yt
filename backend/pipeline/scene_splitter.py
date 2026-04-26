"""
pipeline/scene_splitter.py
──────────────────────────
Uses GPT-4o to split a raw story into narrated scenes.
Each scene gets:
  - narration  : the text to be read aloud
  - image_prompt: a vivid Stable Diffusion / DALL·E prompt for the visual
  - mood        : music mood hint (calm | tense | mysterious | uplifting | dark)
"""

import json
import os
from openai import AsyncOpenAI

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = """You are a professional YouTube story video producer.
Given a story, split it into 6-10 cinematic scenes.
For each scene return a JSON array with objects containing:
  "narration"    : the exact text to narrate (1-3 sentences)
  "image_prompt" : a detailed, cinematic image generation prompt (no text/words in image).
                   Style: dark, moody, photorealistic, 9:16 vertical, dramatic lighting.
                   Include setting, lighting, mood, camera angle.
  "mood"         : one of: calm | tense | mysterious | uplifting | dark | eerie

Rules:
- Cover the full story arc (beginning → middle → end).
- Image prompts must NOT contain any text, letters, words, or signs.
- Keep narration natural for text-to-speech.
- Return ONLY a valid JSON array. No markdown, no explanation."""


async def split_story_into_scenes(story: str) -> list[dict]:
    """
    Returns a list of scene dicts:
    [{ "narration": "...", "image_prompt": "...", "mood": "..." }, ...]
    """
    response = await client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"STORY:\n\n{story}"},
        ],
        temperature=0.7,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content
    data = json.loads(raw)

    # GPT sometimes wraps array in {"scenes": [...]}
    if isinstance(data, dict):
        for key in ("scenes", "scene_list", "result", "data"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        else:
            # fallback: grab first list value
            data = next(v for v in data.values() if isinstance(v, list))

    return data
