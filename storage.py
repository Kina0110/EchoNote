import json

from fastapi import HTTPException

from config import TRANSCRIPTS_DIR, TAGS_FILE, VOICEPRINTS_FILE, SETTINGS_FILE


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
        json.dump(vp, f, indent=2)


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    return {}


def save_settings(settings: dict) -> None:
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)


def iter_transcripts(sort_key=None, reverse=False):
    """Iterate over all saved transcripts, yielding parsed dicts. Skips corrupt files."""
    files = TRANSCRIPTS_DIR.glob("*.json")
    if sort_key == "mtime":
        files = sorted(files, key=lambda p: p.stat().st_mtime, reverse=reverse)
    else:
        files = list(files)
    for f in files:
        try:
            with open(f) as fh:
                yield json.load(fh)
        except (json.JSONDecodeError, KeyError):
            continue
