# Voice Transcriber

A local web app that transcribes audio and video files with automatic speaker detection. Upload recordings, get clean transcripts with speaker labels and timestamps, and copy them straight into ChatGPT.

Runs entirely on your machine. No cloud hosting, no accounts beyond the API keys.

## Features

- **Transcription** — Upload audio/video files or record live in the browser. Automatic speaker diarization with click-to-rename labels.
- **Multi-file combine** — Upload multiple recordings from the same meeting and get one combined transcript with file-boundary markers.
- **Voice recognition** — Speakers are automatically identified using local voice fingerprints ([resemblyzer](https://github.com/resemble-ai/Resemblyzer)). Rename a speaker once and they're recognized in future transcriptions.
- **AI summaries & action items** — GPT-5 generates a summary and extracts action items. Accept, dismiss, or delete action items interactively.
- **Audio/video playback** — Synced player with click-to-seek on any utterance, speed control (1x–2x), and auto-scroll.
- **Search** — Global search across all transcripts, plus in-transcript search with match navigation.
- **Tags & bookmarks** — Color-coded tags for organizing transcripts. Bookmark individual utterances.
- **Export** — Copy for ChatGPT, export as `.txt` or `.srt`. Bulk-copy all transcripts by tag.
- **Live recording** — Record in-browser with progressive backup downloads every 30 seconds.
- **Cost tracking** — Dashboard with Deepgram and GPT usage, per-file breakdown, credit remaining.
- **Mobile friendly** — Dark mode UI optimized for iPhone Safari. Access from any device on your network.

## Prerequisites

- Python 3.11+
- ffmpeg
- [Deepgram API key](https://deepgram.com) (free $200 credit on signup)
- [OpenAI API key](https://platform.openai.com) (optional, for summaries)

## Setup

```bash
git clone https://github.com/Kina0110/voice-transcriber.git
cd voice-transcriber
python -m venv .venv
source .venv/bin/activate        # Mac/Linux — .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env             # Add your API keys
```

## Running

```bash
source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000**. Access from your phone at `http://<your-ip>:8000`.

## Supported Formats

| Type  | Formats                              |
|-------|--------------------------------------|
| Video | MP4, MOV, AVI, MKV, WebM            |
| Audio | MP3, WAV, M4A, OGG, FLAC, AAC, WMA  |

Max file size: 2GB

## Estimated Costs

| Service  | Rate                     | Example                     |
|----------|--------------------------|-----------------------------|
| Deepgram | ~$0.0092/min             | ~$0.28 per 30-min recording |
| GPT-5    | $1.25/1M in, $10/1M out | ~$0.03 per summary          |

The $200 Deepgram free credit covers ~360 hours of audio.

## Tech Stack

- **Backend:** Python / FastAPI
- **Frontend:** Vanilla HTML / CSS / JS (no build step)
- **Transcription:** Deepgram Nova-3 with speaker diarization
- **Summaries:** OpenAI GPT-5
- **Voice Recognition:** resemblyzer (local)
- **Audio processing:** ffmpeg
- **Storage:** JSON files on disk

## Project Structure

```
app.py                 # FastAPI routes
transcription.py       # Shared transcription logic (Deepgram, parsing, errors)
audio.py               # FFmpeg audio extraction & concatenation
ai.py                  # GPT-5 summary & action items
voiceprints.py         # Speaker voice recognition
storage.py             # JSON file I/O
helpers.py             # Text formatting utilities
config.py              # Constants, paths, env vars
static/
  index.html           # Single-page app
  app.js               # Frontend logic
  style.css            # Dark theme styles
```

## Architecture

```
Browser (index.html + app.js)
  │
  ├── Upload file(s) ──► POST /api/transcribe or /api/transcribe-multi
  │                           │
  │                           ├── audio.py: extract audio with ffmpeg → 16kHz mono WAV
  │                           ├── audio.py: concat WAVs (multi-file only)
  │                           ├── transcription.py: send WAV to Deepgram Nova-3
  │                           ├── transcription.py: parse utterances + speakers
  │                           ├── voiceprints.py: match speakers to known voices
  │                           ├── ai.py: generate summary + action items (GPT-5)
  │                           └── storage.py: save transcript JSON to disk
  │
  ├── Browse/search ──► GET /api/transcripts, /api/search
  ├── Playback ──────► GET /api/transcripts/:id/audio or /video
  └── Edit ──────────► PATCH /api/transcripts/:id/speakers, /rename, /tags, etc.
```

**Data flow:** Upload → ffmpeg extracts audio → Deepgram transcribes → voiceprints match speakers → GPT summarizes → JSON saved to `transcripts/`.

**Storage:** No database. Each transcript is a JSON file in `transcripts/`. Audio in `audio/`, videos in `videos/`, voice fingerprints in `voiceprints.json`. All gitignored.

**Transcript JSON shape:**
```json
{
  "id": "uuid",
  "filename": "Meeting.m4a",
  "created_at": "2026-02-26T...",
  "duration_seconds": 1234.56,
  "speakers": { "Speaker 1": "Alice", "Speaker 2": "Bob" },
  "utterances": [
    { "speaker": "Speaker 1", "text": "...", "start": 0.0, "end": 5.2 },
    { "type": "file-boundary", "filename": "part2.mp4", "start": 600.0 }
  ],
  "full_text": "Alice: ...\nBob: ...",
  "audio_file": "uuid.wav",
  "summary": "...",
  "action_items": [{ "text": "...", "status": "pending" }],
  "tags": ["planning"],
  "bookmarks": [0, 5],
  "source_files": ["part1.mp4", "part2.mp4"]
}
```

See [CHANGELOG.md](CHANGELOG.md) for version history.
