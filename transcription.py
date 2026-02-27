import asyncio
import os

import aiofiles
from deepgram import DeepgramClient
from fastapi import HTTPException
from pathlib import Path

from config import ALLOWED_EXTENSIONS, MAX_FILE_SIZE
from helpers import merge_short_utterances


async def save_upload(file, uploads_dir: Path) -> tuple[Path, str]:
    """Save an uploaded file to disk. Returns (upload_path, extension)."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{ext}'. Supported: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    import uuid
    file_id = uuid.uuid4().hex
    upload_path = uploads_dir / f"{file_id}{ext}"

    async with aiofiles.open(upload_path, "wb") as out:
        total = 0
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > MAX_FILE_SIZE:
                raise HTTPException(status_code=413, detail="File too large. Maximum size is 2GB.")
            await out.write(chunk)

    return upload_path, ext


async def transcribe_audio(wav_path: Path) -> dict:
    """Send a WAV file to Deepgram and return the raw result dict."""
    dg_client = DeepgramClient(api_key=os.getenv("DEEPGRAM_API_KEY"))
    with open(wav_path, "rb") as f:
        audio_data = f.read()

    response = await asyncio.to_thread(
        dg_client.listen.v1.media.transcribe_file,
        request=audio_data,
        model="nova-3",
        diarize=True, punctuate=True, paragraphs=True,
        utterances=True, smart_format=True,
    )
    return response.model_dump()


def extract_duration(result: dict) -> float:
    """Extract audio duration from a Deepgram result."""
    if "metadata" in result and "duration" in result["metadata"]:
        return result["metadata"]["duration"]
    if "results" in result and "channels" in result["results"]:
        channels = result["results"]["channels"]
        if channels and "alternatives" in channels[0] and channels[0]["alternatives"]:
            words = channels[0]["alternatives"][0].get("words", [])
            if words:
                return words[-1].get("end", 0)
    return 0


def extract_utterances(result: dict) -> tuple[list, dict]:
    """Extract utterances and speakers from a Deepgram result. Returns (utterances, speakers)."""
    utterances = []
    speaker_set = set()
    raw_utterances = result.get("results", {}).get("utterances", [])

    for u in raw_utterances:
        speaker_label = f"Speaker {u.get('speaker', 0) + 1}"
        speaker_set.add(speaker_label)
        utterances.append({
            "speaker": speaker_label,
            "text": u.get("transcript", ""),
            "start": u.get("start", 0),
            "end": u.get("end", 0),
        })

    # Fallback: build from words if no utterances
    if not utterances:
        channels = result.get("results", {}).get("channels", [])
        if channels:
            words = channels[0].get("alternatives", [{}])[0].get("words", [])
            current_speaker = None
            current_text = []
            current_start = 0
            current_end = 0
            for w in words:
                sp = f"Speaker {w.get('speaker', 0) + 1}"
                if sp != current_speaker:
                    if current_speaker and current_text:
                        speaker_set.add(current_speaker)
                        utterances.append({
                            "speaker": current_speaker,
                            "text": " ".join(current_text),
                            "start": current_start,
                            "end": current_end,
                        })
                    current_speaker = sp
                    current_text = [w.get("punctuated_word", w.get("word", ""))]
                    current_start = w.get("start", 0)
                    current_end = w.get("end", 0)
                else:
                    current_text.append(w.get("punctuated_word", w.get("word", "")))
                    current_end = w.get("end", 0)
            if current_speaker and current_text:
                speaker_set.add(current_speaker)
                utterances.append({
                    "speaker": current_speaker,
                    "text": " ".join(current_text),
                    "start": current_start,
                    "end": current_end,
                })

    utterances = merge_short_utterances(utterances)
    speakers = {s: s for s in sorted(speaker_set)}
    return utterances, speakers


async def attach_summary(transcript: dict) -> None:
    """Generate and attach AI summary + action items to a transcript dict (in-place)."""
    from ai import generate_summary
    summary_result = await asyncio.to_thread(generate_summary, transcript["full_text"])
    if summary_result:
        transcript["summary"] = summary_result["text"]
        transcript["summary_usage"] = {
            "input_tokens": summary_result["input_tokens"],
            "output_tokens": summary_result["output_tokens"],
            "cost": summary_result["cost"],
        }
        transcript["action_items"] = [
            {"text": item, "status": "pending"}
            for item in summary_result.get("action_items", [])
        ]


def handle_transcription_error(e: Exception) -> HTTPException:
    """Convert common transcription errors into appropriate HTTP responses."""
    error_msg = str(e).lower()
    if "invalid credentials" in error_msg or "401" in error_msg:
        return HTTPException(status_code=401, detail="Your Deepgram API key seems invalid. Check your .env file.")
    if "rate limit" in error_msg or "429" in error_msg:
        return HTTPException(status_code=429, detail="Too many requests. Wait a moment and try again.")
    if "too short" in error_msg:
        return HTTPException(status_code=400, detail="The audio file seems too short to transcribe.")
    if "connection" in error_msg or "network" in error_msg or "resolve" in error_msg:
        return HTTPException(status_code=502, detail="Couldn't reach Deepgram. Check your internet connection.")
    return HTTPException(status_code=500, detail=f"Transcription failed: {str(e)[:300]}")


def cleanup_files(*paths: Path) -> None:
    """Delete files, ignoring errors."""
    for p in paths:
        try:
            if p and p.exists():
                p.unlink()
        except Exception:
            pass
