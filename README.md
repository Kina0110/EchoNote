# Voice Transcriber

Local web app for transcribing audio/video files with speaker diarization. Upload a recording, get a clean transcript with speaker labels, copy it into ChatGPT for Q&A.

## Prerequisites

- Python 3.11 or newer
- ffmpeg installed on your system
- A Deepgram account (free $200 credit on signup)

## Setup

```bash
# 1. Create a virtual environment
cd voice-transcriber
python -m venv venv

# 2. Activate it
# Mac/Linux:
source venv/bin/activate
# Windows:
# venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install ffmpeg (if you don't have it)
# Mac:
brew install ffmpeg
# Windows:
# winget install ffmpeg
# Or download from https://ffmpeg.org/download.html

# 5. Set up your API key
cp .env.example .env
# Open .env and paste your Deepgram API key

# 6. Run the app
python app.py
```

Open http://localhost:8000 in your browser.

## Getting Your Deepgram API Key

1. Go to https://deepgram.com and sign up (free)
2. You get $200 in free credits — enough for ~430 hours of transcription
3. Go to Dashboard > API Keys > Create a New Key
4. Copy the key and paste it into your `.env` file

## Accessing from iPhone (Optional)

1. Install [Tailscale](https://tailscale.com/download) on your laptop
2. Install Tailscale on your iPhone from the App Store
3. Sign into the same Tailscale account on both devices
4. Find your laptop's Tailscale name (e.g., "my-laptop")
5. On your iPhone, open Safari and go to: `http://my-laptop:8000`

## Supported Formats

**Video:** MP4, MOV, AVI, MKV, WebM
**Audio:** MP3, WAV, M4A, OGG, FLAC, AAC, WMA

Max file size: 2GB

## Estimated Costs

- Deepgram gives you $200 free credit on signup
- Rate: ~$0.0092/minute (transcription + diarization)
- ~$0.28 per 30-minute recording
- The free credit covers roughly 430 hours of audio

## Tech Stack

- **Backend:** Python / FastAPI
- **Frontend:** Vanilla HTML/CSS/JS (no build step)
- **Transcription:** Deepgram Nova-3
- **Storage:** JSON files on disk (no database)
