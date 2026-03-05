import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
TRANSCRIPTS_DIR = BASE_DIR / "transcripts"
AUDIO_DIR = BASE_DIR / "audio"
VIDEOS_DIR = BASE_DIR / "videos"
STATIC_DIR = BASE_DIR / "static"
TAGS_FILE = BASE_DIR / "tags.json"
VOICEPRINTS_FILE = BASE_DIR / "voiceprints.json"
SETTINGS_FILE = BASE_DIR / "settings.json"

UPLOADS_DIR.mkdir(exist_ok=True)
TRANSCRIPTS_DIR.mkdir(exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)
VIDEOS_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma",
}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

COST_PER_MINUTE = 0.0092  # Deepgram Nova-3 + diarization
STARTING_CREDIT = 200.0

AI_INPUT_COST_PER_TOKEN = 1.25 / 1_000_000   # $1.25 per 1M input tokens (GPT-5)
AI_OUTPUT_COST_PER_TOKEN = 10.00 / 1_000_000  # $10.00 per 1M output tokens

SPEAKER_COLORS = [
    "#58a6ff", "#f78166", "#7ee787", "#d2a8ff",
    "#ff7b72", "#79c0ff", "#ffa657", "#a5d6ff",
]

TAG_COLORS = [
    "#e6b450", "#e06c9f", "#56d4bc", "#c49bff", "#f0883e",
    "#63bfdb", "#e66767", "#8ddb8c", "#b8a9c9", "#d4a76a",
]

VIDEO_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
}


def check_ffmpeg():
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, check=True, timeout=10,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def check_api_key():
    return bool(os.getenv("DEEPGRAM_API_KEY"))


ffmpeg_available = check_ffmpeg()
api_key_configured = check_api_key()
