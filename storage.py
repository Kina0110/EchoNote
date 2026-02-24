import json

from fastapi import HTTPException

from config import TRANSCRIPTS_DIR, TAGS_FILE, VOICEPRINTS_FILE


def load_transcript(transcript_id: str) -> dict:
    path = TRANSCRIPTS_DIR / f"{transcript_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Transcript not found")
    with open(path) as f:
        return json.load(f)


def save_transcript(transcript: dict) -> None:
    path = TRANSCRIPTS_DIR / f"{transcript['id']}.json"
    with open(path, "w") as f:
        json.dump(transcript, f, indent=2)


def load_tags() -> dict:
    if TAGS_FILE.exists():
        with open(TAGS_FILE) as f:
            return json.load(f)
    return {}


def save_tags(tags: dict) -> None:
    with open(TAGS_FILE, "w") as f:
        json.dump(tags, f, indent=2)


def load_voiceprints() -> dict:
    if VOICEPRINTS_FILE.exists():
        with open(VOICEPRINTS_FILE) as f:
            return json.load(f)
    return {}


def save_voiceprints(vp: dict) -> None:
    with open(VOICEPRINTS_FILE, "w") as f:
        json.dump(vp, f)
