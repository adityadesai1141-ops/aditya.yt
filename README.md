# StoryReel — Faceless YouTube Video Generator

Turn any text story into a fully produced 1080×1920 MP4 video, automatically:
- AI voiceover (ElevenLabs or OpenAI TTS)
- Cinematic scene images (DALL·E 3 or Stability AI)
- Ken Burns zoom/pan motion on every scene
- Word-by-word burnt-in subtitles (Whisper)
- Background music mixed under voice (Freesound)
- Direct YouTube upload via Data API v3

---

## Requirements

- Python 3.11+
- FFmpeg installed and on PATH (`brew install ffmpeg` / `apt install ffmpeg`)
- API keys (see `.env.example`)

---

## Quick start

### 1. Clone & set up environment

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure API keys

```bash
cp ../.env.example .env
# Edit .env and fill in your API keys
```

**Minimum required:** `OPENAI_API_KEY` (covers GPT-4o + DALL·E 3 + Whisper + TTS fallback)

**Recommended:** Also add `ELEVENLABS_API_KEY` for better voiceover quality.

**Optional:** `FREESOUND_API_KEY` for real music; `STABILITY_API_KEY` for cheaper images.

### 3. Run the backend

```bash
uvicorn main:app --reload --port 8000
```

API docs available at: http://localhost:8000/docs

### 4. Open the frontend

Open `frontend/index.html` in your browser directly, or serve it:

```bash
cd ../frontend
python -m http.server 3000
# then open http://localhost:3000
```

---

## YouTube upload setup

1. Go to https://console.cloud.google.com
2. Create a new project
3. Enable **YouTube Data API v3**
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**
5. Choose **Desktop app**, download `client_secrets.json`
6. Place `client_secrets.json` in the `/backend` folder
7. First upload will open a browser for OAuth consent — subsequent runs use the saved token

---

## API reference

| Method | Endpoint              | Description                        |
|--------|-----------------------|------------------------------------|
| POST   | `/generate`           | Start a new video generation job   |
| GET    | `/status/{job_id}`    | SSE stream of progress (0–100%)    |
| GET    | `/download/{job_id}`  | Download the finished MP4          |
| POST   | `/upload/{job_id}`    | Upload to YouTube                  |
| GET    | `/health`             | Health check                       |

### POST /generate

```json
{ "story": "Your full story text here..." }
```

Returns:
```json
{ "job_id": "uuid-...", "message": "Pipeline started" }
```

### GET /status/{job_id} — SSE events

```
data: {"step": "Splitting story into scenes", "progress": 5, "error": null}
data: {"step": "Generating voiceover, visuals & music", "progress": 10, "error": null}
...
data: {"step": "complete", "progress": 100, "error": null}
```

---

## Pipeline stages

```
Story text
   │
   ▼
[GPT-4o] Scene splitter
   │  → 6-10 scenes, each with narration + image_prompt + mood
   │
   ├──────────────────────────────────────┐
   ▼                                      ▼
[ElevenLabs / OpenAI TTS]      [DALL·E 3 / Stability AI]
Per-scene MP3 voiceover         Per-scene PNG image
   │                                      │
   └──────────────┬───────────────────────┘
                  │           ▼
            [Freesound]  Background music MP3
                  │
                  ▼
          [OpenAI Whisper]
          Word-level SRT subtitles per scene
                  │
                  ▼
            [FFmpeg]
            ├── Ken Burns zoom/pan on each image
            ├── Burn subtitles into video
            ├── xfade crossfade between scenes
            ├── Mix music at 18% volume under voice
            └── Export 1080×1920 H.264/AAC MP4
                  │
                  ▼
           Output MP4
           ├── Download
           └── YouTube upload (OAuth)
```

---

## Configuration options (`.env`)

| Variable                  | Default  | Description                             |
|---------------------------|----------|-----------------------------------------|
| `OPENAI_API_KEY`          | required | GPT-4o + DALL·E + Whisper               |
| `ELEVENLABS_API_KEY`      | optional | Better TTS voices                       |
| `ELEVENLABS_VOICE_ID`     | Adam     | ElevenLabs voice to use                 |
| `STABILITY_API_KEY`       | optional | Alternative to DALL·E for images        |
| `FREESOUND_API_KEY`       | optional | Real background music                   |
| `VIDEO_WIDTH`             | 1080     | Output width (px)                       |
| `VIDEO_HEIGHT`            | 1920     | Output height (px)                      |
| `VIDEO_FPS`               | 30       | Frames per second                       |
| `MUSIC_VOLUME`            | 0.18     | Music volume relative to voice (0–1)    |
| `SCENE_TRANSITION_SECS`   | 0.5      | Crossfade duration between scenes       |

---

## Cost estimate (per video, ~10 scenes)

| Service         | Usage              | Approx cost   |
|-----------------|--------------------|---------------|
| GPT-4o          | ~2k tokens         | ~$0.01        |
| DALL·E 3        | 10 images          | ~$0.40        |
| ElevenLabs      | ~500 chars/scene   | ~$0.50        |
| Whisper         | ~3 min audio       | ~$0.02        |
| **Total**       |                    | **~$0.93**    |

(Stability AI instead of DALL·E cuts image cost by ~80%)
