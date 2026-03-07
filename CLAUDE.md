# CLAUDE.md

## Project
Transcribbly (EchoNote) — voice transcription web app. Upload audio/video, get speaker-diarized transcripts with AI summaries, chapters, action items, and chat.

## Tech Stack
- **Backend**: Python FastAPI (`app.py`), uvicorn on port 8000
- **Frontend**: Vanilla JS, HTML, CSS (no framework, no bundler)
- **Transcription**: Deepgram Nova-3 API
- **AI**: OpenAI GPT-5 (summaries/chapters), GPT-5 mini (chat)
- **Storage**: JSON files in `transcripts/`, one per transcript

## How to Run
```
python app.py
```
No hot reload — restart server after backend changes.

## File Structure
```
app.py           — FastAPI routes
ai.py            — OpenAI API calls (summary, chapters, chat)
transcription.py — Deepgram transcription logic
audio.py         — FFmpeg audio processing
storage.py       — File I/O helpers
helpers.py       — Shared utilities
config.py        — App configuration
static/
  index.html     — Single-page app HTML
  app.js         — All frontend JS (sectioned with // === Section ===)
  style.css      — All styles
```

## Conventions
- **Cache busting**: Static assets use `?v=N` query params — bump on every change
- **Sections**: JS and CSS are organized with `// === Section ===` comment headers
- **Transcript JSON**: All transcript data (text, speakers, summary, chapters, action items, chat threads, comments, bookmarks) stored in a single JSON file per transcript
- **Read-modify-write**: All transcript mutations follow `load_transcript()` → modify → `save_transcript()` pattern

## Code Standards
- Write clean, concise, modularized code
- Create new files to abstract logic when a file grows too large — don't let files balloon
- Reuse existing utilities before creating new ones
- Always update `CHANGELOG.md` after adding features or making notable changes

## Git
- Always ask before running `git commit` or `git push`
- Don't modify `.env` files
