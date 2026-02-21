# Voice Transcriber — Claude Code Build Spec

## Overview
Build a local web app for transcribing uploaded audio/video files with speaker diarization. User uploads recordings, gets clean transcripts with speaker labels, then copies the transcript to paste into ChatGPT for Q&A. No built-in chatbot. Runs locally on the user's laptop, accessed via browser (including iPhone via Tailscale).

## Tech Stack
- **Backend:** Python 3.11+ with FastAPI
- **Frontend:** Simple HTML/CSS/JS (no framework, no React, no build step)
- **Transcription:** Deepgram Nova-3 API (with speaker diarization)
- **File storage:** Local filesystem
- **No database** — JSON files on disk are fine

## Project Structure
```
voice-transcriber/
├── app.py                 # FastAPI backend
├── .env                   # API keys (user fills in)
├── .env.example           # Template showing required keys
├── requirements.txt       # Python dependencies
├── transcripts/           # Saved transcripts (JSON)
├── uploads/               # Temporary audio/video uploads (cleared after processing)
├── static/
│   ├── index.html         # Main UI
│   ├── style.css          # Styles
│   └── app.js             # Frontend logic
└── README.md
```

## Dependencies (requirements.txt)
```
fastapi
uvicorn
python-dotenv
python-multipart
deepgram-sdk
aiofiles
ffmpeg-python
```

## Environment Variables (.env)
```
DEEPGRAM_API_KEY=your_key_here
```

That's it. One API key. Simple.

## Important: Video File Handling
Users will be uploading VIDEO files (mp4, mov, webm, etc.) not just audio. The app MUST:
1. Accept both audio AND video file uploads
2. Use ffmpeg to extract the audio track from video files before sending to Deepgram
3. Convert extracted audio to a Deepgram-friendly format (wav or mp3, 16kHz mono is ideal)
4. Send only the extracted audio to Deepgram (not the full video file — saves bandwidth and processing time)
5. Clean up temporary files after processing

**ffmpeg must be installed on the user's system.** The README should include instructions for installing ffmpeg on Mac (`brew install ffmpeg`) and Windows (`winget install ffmpeg` or download from ffmpeg.org).

Supported input formats: mp4, mov, avi, mkv, webm, mp3, wav, m4a, ogg, flac, aac, wma

## Core Features & API Endpoints

### 1. Upload & Transcribe
**Endpoint:** `POST /api/transcribe`
- Accepts audio/video file upload via multipart form
- Max file size: 2GB (video files can be large)
- Processing steps:
  1. Save uploaded file to `uploads/` temporarily
  2. Use ffmpeg to extract audio → convert to wav (16kHz mono)
  3. Send extracted audio to Deepgram Nova-3 with these options:
     - `model="nova-3"`
     - `diarize=True`
     - `punctuate=True`
     - `paragraphs=True`
     - `utterances=True`
     - `smart_format=True`
  4. Parse response into structured transcript
  5. Save transcript as JSON in `transcripts/`
  6. Delete temporary upload and extracted audio files
  7. Return transcript to frontend

**Transcript JSON format:**
```json
{
  "id": "uuid-here",
  "filename": "meeting-recording.mp4",
  "created_at": "2026-02-20T10:30:00",
  "duration_seconds": 1800,
  "speakers": {
    "Speaker 1": "Speaker 1",
    "Speaker 2": "Speaker 2"
  },
  "utterances": [
    {
      "speaker": "Speaker 1",
      "text": "Let's discuss the Q3 budget.",
      "start": 0.5,
      "end": 2.3
    },
    {
      "speaker": "Speaker 2",
      "text": "Sure, I've prepared the numbers.",
      "start": 2.8,
      "end": 4.1
    }
  ],
  "full_text": "Speaker 1: Let's discuss the Q3 budget.\nSpeaker 2: Sure, I've prepared the numbers.\n..."
}
```

The `speakers` dict maps original labels to display names (for renaming).
The `full_text` field is the pre-formatted version for easy copy-paste into ChatGPT.

### 2. List Past Transcripts
**Endpoint:** `GET /api/transcripts`
- Reads all JSON files from `transcripts/`
- Returns list with id, filename, date, duration, number of speakers
- Sorted by date (newest first)

### 3. Get a Specific Transcript
**Endpoint:** `GET /api/transcripts/{id}`
- Returns the full transcript JSON

### 4. Delete a Transcript
**Endpoint:** `DELETE /api/transcripts/{id}`
- Deletes the JSON file

### 5. Rename Speakers
**Endpoint:** `PATCH /api/transcripts/{id}/speakers`
- Request body: `{ "Speaker 1": "Alice", "Speaker 2": "Bob" }`
- Updates the `speakers` mapping in the transcript JSON
- Regenerates `full_text` with new speaker names
- Returns updated transcript

### 6. Merge Speakers
**Endpoint:** `POST /api/transcripts/{id}/merge-speakers`
- Request body: `{ "keep": "Speaker 1", "merge": "Speaker 3" }`
- Merges all utterances labeled "Speaker 3" into "Speaker 1"
- Updates the utterances array, speakers dict, and regenerates full_text
- Returns updated transcript
- **Why this matters:** Diarization frequently splits one person into two speakers, especially if they change tone, volume, or pause for a long time. This lets the user fix that with one click instead of manually editing.

### 7. Cost Tracker
**Endpoint:** `GET /api/stats`
- Reads all transcript JSON files and calculates:
  - Total minutes transcribed this month
  - Total minutes transcribed all-time
  - Estimated cost this month (minutes × $0.0092 for transcription + diarization)
  - Estimated cost all-time
  - Number of files this month
  - Number of files all-time
  - Estimated Deepgram credit remaining (start with $200, subtract all-time cost)
- Returns all stats as JSON

**Endpoint:** `GET /api/stats/per-file`
- Returns a list of all transcripts with per-file cost breakdown:
  - filename, date, duration in minutes, estimated cost
- Sorted by date (newest first)

### 8. Copy-Ready Transcript
**Endpoint:** `GET /api/transcripts/{id}/copytext`
- Returns a plain text version formatted specifically for pasting into ChatGPT
- Format:
```
TRANSCRIPT: meeting-recording.mp4
Duration: 30 minutes, 15 seconds
Speakers: Alice, Bob
Date: February 20, 2026

---

[00:00] Alice: Let's discuss the Q3 budget.
[00:02] Bob: Sure, I've prepared the numbers.
[00:05] Alice: Great, walk me through them.
...
```

This format gives ChatGPT all the context it needs to answer questions well.

## Frontend UI

### Design
- Clean, minimal, modern design
- Dark mode by default
- Mobile-responsive (MUST work well on iPhone Safari — user will access via Tailscale)
- No framework — vanilla HTML/CSS/JS
- System font stack (no external fonts)
- Touch-friendly buttons and interactions for mobile use

### Layout — Two Main Views:

#### View 1: Home / Upload
- App name at top (something like "Transcriber" with a simple icon)
- Large drag-and-drop upload zone in the center
  - Also clickable to open file picker
  - Shows accepted formats: "MP4, MOV, MP3, WAV, M4A, and more"
  - Shows max file size
- Upload progress bar when uploading
- Processing status when Deepgram is working:
  - "Uploading file..." → "Extracting audio..." → "Transcribing with AI..." → "Done!"
  - Show a spinner or progress indicator during each step
- Below the upload zone: list of past transcripts as cards
  - Each card shows: filename, date, duration, number of speakers
  - Click a card to open that transcript
  - Swipe or button to delete (with confirmation)

#### View 2: Transcript View
- **Header section:**
  - Filename (shown but not editable)
  - Date and duration
  - Speaker legend with color-coded dots
    - Click a speaker name to rename them (inline edit or small modal)
    - Each speaker gets a distinct color

- **Main transcript area (scrollable):**
  - Each utterance as its own block
  - Timestamp on the left (e.g., [00:05])
  - Speaker name in their color
  - Text of what they said
  - Should feel like reading a conversation/chat

- **Speaker merge UI:**
  - Next to the speaker legend, a "Merge speakers" button
  - When clicked, shows a simple UI: two dropdowns — "Merge [Speaker X] into [Speaker Y]"
  - After merging, the transcript updates immediately
  - Undo option (keeps the original speaker data so you can un-merge if you made a mistake)
  - **Visual hint:** If two speakers have very few utterances, subtly suggest they might be the same person (e.g., "Speaker 3 only spoke 2 times — same person as Speaker 1?")

- **Action buttons (sticky top or bottom bar):**
  - **"Copy for ChatGPT" button** — THE most important button. Big, obvious, prominent.
    - Copies the formatted transcript text to clipboard
    - Shows a brief "Copied!" confirmation toast
    - On mobile, this should be very easy to tap
  - **"Export .txt" button** — downloads transcript as a .txt file
  - **"Export .srt" button** — downloads as subtitle file (nice-to-have)
  - **"Back" button** — return to home/upload view

#### View 3: Cost Tracker (accessible from home page)
- Small icon or link on the home page (e.g., "Usage & Costs" in the top right)
- Shows a clean dashboard with:
  - **This month:** X files, Y minutes, ~$Z.ZZ estimated cost
  - **All time:** X files, Y minutes, ~$Z.ZZ estimated cost
  - **Deepgram credit remaining:** ~$XXX.XX (starts at $200, subtracts all-time costs)
  - **Estimated months of credit left** based on current monthly usage rate
  - Simple bar or progress indicator showing how much credit is used vs remaining
- Below the summary: a table of recent transcriptions with per-file cost
  - Columns: filename, date, duration, estimated cost
- Keep it simple and clean — this is a glance-at-it-occasionally page, not a full analytics dashboard

### Important UI Details
- The "Copy for ChatGPT" button should be the star of the show. Make it big, make it obvious. This is the user's main action after getting a transcript.
- When uploading, show clear status updates at each step. Video files can take a moment to extract audio.
- Speaker colors should be distinct and accessible (good contrast in dark mode)
- On mobile:
  - The layout stacks vertically
  - Buttons are large enough to tap easily
  - The transcript scrolls smoothly
  - Drag-and-drop becomes a file picker button (drag-drop doesn't work well on mobile)
- Add a simple toast/notification system for success/error feedback
- No loading of external resources — everything works offline except the Deepgram API call

## Error Handling
- **Missing API key:** On first launch, if no DEEPGRAM_API_KEY in .env, show a friendly setup screen explaining how to get one
- **ffmpeg not installed:** Check for ffmpeg on startup. If missing, show a clear error with install instructions
- **Unsupported file format:** Show error with list of supported formats
- **File too large:** Show error with size limit
- **Deepgram API error:** Show user-friendly error (not raw traceback). Common errors:
  - Invalid API key → "Your Deepgram API key seems invalid. Check your .env file."
  - Rate limit → "Too many requests. Wait a moment and try again."
  - Audio too short → "The audio file seems too short to transcribe."
- **Network error:** "Couldn't reach Deepgram. Check your internet connection."
- All errors should appear as toast notifications or inline messages, never raw console errors

## How to Run (README.md)
Generate a clear, beginner-friendly README.md with:

### Prerequisites
- Python 3.11 or newer
- ffmpeg installed on your system
- A Deepgram account (free $200 credit on signup)

### Setup Steps
```bash
# 1. Clone or download the project
cd voice-transcriber

# 2. Create a virtual environment
python -m venv venv

# 3. Activate it
# Mac/Linux:
source venv/bin/activate
# Windows:
venv\Scripts\activate

# 4. Install dependencies
pip install -r requirements.txt

# 5. Install ffmpeg (if you don't have it)
# Mac:
brew install ffmpeg
# Windows:
winget install ffmpeg
# Or download from https://ffmpeg.org/download.html

# 6. Set up your API key
cp .env.example .env
# Open .env in any text editor and paste your Deepgram API key

# 7. Run the app
python app.py

# 8. Open in your browser
# Go to http://localhost:8000
```

### Getting Your Deepgram API Key
1. Go to https://deepgram.com and sign up (free)
2. You'll get $200 in free credits — that's enough for ~430 hours of transcription
3. Go to Dashboard → API Keys → Create a New Key
4. Copy the key and paste it into your .env file

### Accessing from iPhone (Optional)
1. Install Tailscale on your laptop: https://tailscale.com/download
2. Install Tailscale on your iPhone from the App Store
3. Sign into the same Tailscale account on both devices
4. Find your laptop's Tailscale name (e.g., "my-laptop")
5. On your iPhone, open Safari and go to: http://my-laptop:8000
6. That's it — works from anywhere, even outside your home

### Estimated Costs
- Deepgram gives you $200 free credit on signup
- After that: ~$4.42/month for 16 files × 30 min each
- That's about $0.28 per recording
- The free credit alone covers roughly 430 hours — that's about 2+ years of your usage

### Future: Swapping to Voxtral (Optional)
If you want to try Mistral's Voxtral transcription later (cheaper at $0.003/min, potentially more accurate), the app is designed so the transcription backend can be swapped easily. The transcript format stays the same regardless of which service does the transcription.

## Nice-to-Haves (implement if straightforward)
- Search through transcript text (simple text filter)
- Keyboard shortcut: Cmd/Ctrl+C on transcript page copies formatted text
- Dark/light mode toggle
- Show estimated cost per transcription (calculate from file duration)
- Drag to reorder or merge speaker labels if diarization split one person into two speakers

## What NOT to Build
- No chatbot / no AI Q&A (user will paste into ChatGPT)
- No user authentication (local only)
- No database (JSON files on disk)
- No real-time/live recording (file upload only)
- No audio/video playback synced to transcript
- No WebSocket streaming
- No Voxtral integration yet (Deepgram only for now — Voxtral can be added later)
