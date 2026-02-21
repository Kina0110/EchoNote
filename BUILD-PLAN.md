# Voice Transcriber — Build Plan

## Overview
Local web app: upload audio/video → Deepgram Nova-3 transcription with speaker diarization → copy-paste into ChatGPT.

## Files to Create

### 1. Config Files
- `requirements.txt` — Python dependencies
- `.env.example` — Template with `DEEPGRAM_API_KEY=your_key_here`

### 2. Backend — `app.py`
Single FastAPI file handling all logic:

**Startup checks:**
- Verify ffmpeg is installed (subprocess check)
- Verify DEEPGRAM_API_KEY is set
- Create `uploads/` and `transcripts/` dirs if missing

**Endpoints:**
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/transcribe` | Upload file → ffmpeg extract audio → Deepgram → save JSON |
| GET | `/api/transcripts` | List all saved transcripts (summary) |
| GET | `/api/transcripts/{id}` | Get full transcript |
| DELETE | `/api/transcripts/{id}` | Delete transcript JSON |
| PATCH | `/api/transcripts/{id}/speakers` | Rename speakers |
| POST | `/api/transcripts/{id}/merge-speakers` | Merge two speakers into one |
| GET | `/api/transcripts/{id}/copytext` | Plain text formatted for ChatGPT |
| GET | `/api/stats` | Monthly/all-time usage & cost stats |
| GET | `/api/stats/per-file` | Per-file cost breakdown |

**Key implementation details:**
- ffmpeg via `subprocess.run` (not ffmpeg-python lib — simpler, fewer deps)
- Deepgram SDK v3 for transcription
- UUID-based transcript IDs
- JSON files in `transcripts/` dir
- Temp files cleaned up after processing
- 2GB max upload size
- SSE or polling for upload progress status

### 3. Frontend — `static/`

**`index.html`** — Single page app with three views:
- Home/Upload view
- Transcript detail view
- Cost tracker view

**`style.css`** — Dark mode default, mobile-first responsive:
- CSS custom properties for theming
- System font stack
- Touch-friendly sizing (min 44px tap targets)
- Smooth transitions

**`app.js`** — Vanilla JS handling:
- File upload with drag-drop + progress
- View routing (hash-based SPA)
- Transcript rendering with speaker colors
- Speaker rename (inline edit)
- Speaker merge UI with suggestion for low-utterance speakers
- Copy to clipboard (Clipboard API)
- Export .txt / .srt download
- Toast notification system
- Cost tracker dashboard

### 4. `README.md`
- Prerequisites, setup steps, Deepgram key instructions
- ffmpeg install for Mac/Windows
- Tailscale iPhone access guide
- Cost estimates

## Build Order
1. Config files (requirements.txt, .env.example)
2. Backend (app.py) — all endpoints
3. Frontend (index.html, style.css, app.js)
4. README.md
5. Test locally

## Architecture Decisions
- **No ffmpeg-python lib** — just shell out to ffmpeg directly. Simpler, one less abstraction.
- **No database** — JSON files on disk per spec.
- **Hash-based SPA routing** — `#/`, `#/transcript/{id}`, `#/costs`. No page reloads.
- **SSE for transcription progress** — backend streams status updates during processing.
- **Speaker colors** — predefined palette of 8 distinct colors, cycle if more speakers.
