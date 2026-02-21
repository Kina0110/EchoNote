import asyncio
import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import re

import aiofiles
from deepgram import DeepgramClient
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from starlette.responses import FileResponse

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
TRANSCRIPTS_DIR = BASE_DIR / "transcripts"
AUDIO_DIR = BASE_DIR / "audio"
STATIC_DIR = BASE_DIR / "static"
TAGS_FILE = BASE_DIR / "tags.json"

UPLOADS_DIR.mkdir(exist_ok=True)
TRANSCRIPTS_DIR.mkdir(exist_ok=True)
AUDIO_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".webm",
    ".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma",
}
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB

COST_PER_MINUTE = 0.0092  # Deepgram Nova-3 + diarization
STARTING_CREDIT = 200.0

# Speaker color palette (used by frontend, defined here for consistency)
SPEAKER_COLORS = [
    "#58a6ff", "#f78166", "#7ee787", "#d2a8ff",
    "#ff7b72", "#79c0ff", "#ffa657", "#a5d6ff",
]

TAG_COLORS = [
    "#e6b450", "#e06c9f", "#56d4bc", "#c49bff", "#f0883e",
    "#63bfdb", "#e66767", "#8ddb8c", "#b8a9c9", "#d4a76a",
]

app = FastAPI(title="Voice Transcriber")

# --- Startup checks ---

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


@app.get("/api/health")
async def health():
    return {
        "ffmpeg": ffmpeg_available,
        "api_key": api_key_configured,
    }


# --- Helper functions ---

def extract_audio(input_path: Path, output_path: Path) -> None:
    """Use ffmpeg to extract audio from a media file and convert to 16kHz mono WAV."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vn",                    # no video
        "-acodec", "pcm_s16le",   # 16-bit PCM
        "-ar", "16000",           # 16kHz
        "-ac", "1",               # mono
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.decode(errors='replace')[:500]}")


def format_timestamp(seconds: float) -> str:
    """Format seconds as [MM:SS] or [H:MM:SS]."""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"[{h}:{m:02d}:{s:02d}]"
    return f"[{m:02d}:{s:02d}]"


def merge_short_utterances(utterances: list, min_words: int = 8, max_gap: float = 5.0) -> list:
    """Merge consecutive same-speaker utterances that are too short, within a time gap."""
    if not utterances:
        return utterances
    merged = [dict(utterances[0])]
    for u in utterances[1:]:
        prev = merged[-1]
        prev_words = len(prev["text"].split())
        same_speaker = u["speaker"] == prev["speaker"]
        gap = u["start"] - prev["end"]
        if same_speaker and prev_words < min_words and gap <= max_gap:
            prev["text"] = prev["text"].rstrip(",. ") + " " + u["text"]
            prev["end"] = u["end"]
        else:
            merged.append(dict(u))
    return merged


def generate_full_text(utterances: list, speakers: dict) -> str:
    """Build the full_text field from utterances with speaker name mapping."""
    lines = []
    for u in utterances:
        display_name = speakers.get(u["speaker"], u["speaker"])
        lines.append(f"{display_name}: {u['text']}")
    return "\n".join(lines)


def generate_copy_text(transcript: dict) -> str:
    """Generate the ChatGPT-friendly copy text."""
    speakers = transcript["speakers"]
    speaker_names = list(set(speakers.values()))
    duration_s = transcript.get("duration_seconds", 0)
    h, remainder = divmod(int(duration_s), 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        duration_str = f"{h} hour{'s' if h != 1 else ''}, {m} minute{'s' if m != 1 else ''}, {s} second{'s' if s != 1 else ''}"
    elif m > 0:
        duration_str = f"{m} minute{'s' if m != 1 else ''}, {s} second{'s' if s != 1 else ''}"
    else:
        duration_str = f"{s} second{'s' if s != 1 else ''}"

    created = datetime.fromisoformat(transcript["created_at"])
    date_str = created.strftime("%B %d, %Y")

    lines = [
        f"TRANSCRIPT: {transcript['filename']}",
        f"Duration: {duration_str}",
        f"Speakers: {', '.join(speaker_names)}",
        f"Date: {date_str}",
        "",
        "---",
        "",
    ]

    for u in transcript["utterances"]:
        display_name = speakers.get(u["speaker"], u["speaker"])
        ts = format_timestamp(u["start"])
        lines.append(f"{ts} {display_name}: {u['text']}")

    return "\n".join(lines)


KIMI_INPUT_COST_PER_TOKEN = 0.0028 / 1000   # ~$0.0028 per 1K input tokens
KIMI_OUTPUT_COST_PER_TOKEN = 0.0084 / 1000  # ~$0.0084 per 1K output tokens


def generate_summary(full_text: str) -> dict | None:
    """Generate a 2-3 sentence summary using Kimi K2 API. Returns dict with summary and usage, or None."""
    api_key = os.getenv("KIMI_API_KEY")
    if not api_key:
        return None
    try:
        client = OpenAI(api_key=api_key, base_url="https://api.moonshot.ai/v1")
        # Truncate to avoid huge prompts (first ~4000 chars is plenty for summary)
        text = full_text[:4000]
        resp = client.chat.completions.create(
            model="kimi-k2-0905-preview",
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Summarize the following transcript in 2-3 concise sentences. Focus on the main topics discussed and any key decisions or takeaways. Do not start with 'This transcript' or 'In this conversation' — just state what happened directly."},
                {"role": "user", "content": text},
            ],
            max_tokens=200,
            temperature=0.3,
        )
        usage = resp.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        cost = (input_tokens * KIMI_INPUT_COST_PER_TOKEN) + (output_tokens * KIMI_OUTPUT_COST_PER_TOKEN)
        return {
            "text": resp.choices[0].message.content.strip(),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": round(cost, 6),
        }
    except Exception:
        return None


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


# --- API Endpoints ---

@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...)):
    if not ffmpeg_available:
        raise HTTPException(status_code=500, detail="ffmpeg is not installed. Please install it: brew install ffmpeg")
    if not api_key_configured:
        raise HTTPException(status_code=500, detail="DEEPGRAM_API_KEY not set. Copy .env.example to .env and add your key.")

    # Validate file extension
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{ext}'. Supported: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # Save uploaded file
    file_id = uuid.uuid4().hex
    upload_path = UPLOADS_DIR / f"{file_id}{ext}"
    wav_path = UPLOADS_DIR / f"{file_id}.wav"

    try:
        # Stream upload to disk
        async with aiofiles.open(upload_path, "wb") as out:
            total = 0
            while chunk := await file.read(1024 * 1024):  # 1MB chunks
                total += len(chunk)
                if total > MAX_FILE_SIZE:
                    raise HTTPException(status_code=413, detail="File too large. Maximum size is 2GB.")
                await out.write(chunk)

        # Extract audio with ffmpeg
        await asyncio.to_thread(extract_audio, upload_path, wav_path)

        # Send to Deepgram
        dg_client = DeepgramClient(api_key=os.getenv("DEEPGRAM_API_KEY"))

        with open(wav_path, "rb") as audio_file:
            audio_data = audio_file.read()

        response = await asyncio.to_thread(
            dg_client.listen.v1.media.transcribe_file,
            request=audio_data,
            model="nova-3",
            diarize=True,
            punctuate=True,
            paragraphs=True,
            utterances=True,
            smart_format=True,
        )

        # Parse response — Deepgram SDK v5 returns a Pydantic model
        result = response.model_dump()

        # Extract duration
        duration = 0
        if "metadata" in result and "duration" in result["metadata"]:
            duration = result["metadata"]["duration"]
        elif "results" in result and "channels" in result["results"]:
            channels = result["results"]["channels"]
            if channels and "alternatives" in channels[0] and channels[0]["alternatives"]:
                words = channels[0]["alternatives"][0].get("words", [])
                if words:
                    duration = words[-1].get("end", 0)

        # Extract utterances
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

        # If no utterances from utterances field, try building from words
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

        # Merge short utterances into longer ones
        utterances = merge_short_utterances(utterances)

        # Build speakers mapping
        speakers = {s: s for s in sorted(speaker_set)}

        # Build transcript
        transcript_id = str(uuid.uuid4())

        # Keep the extracted audio file
        audio_filename = f"{transcript_id}.wav"
        audio_dest = AUDIO_DIR / audio_filename
        shutil.move(str(wav_path), str(audio_dest))

        transcript = {
            "id": transcript_id,
            "filename": file.filename or "unknown",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(duration, 2),
            "speakers": speakers,
            "utterances": utterances,
            "full_text": generate_full_text(utterances, speakers),
            "audio_file": audio_filename,
        }

        # Generate summary (non-blocking, don't fail if it errors)
        summary_result = await asyncio.to_thread(generate_summary, transcript["full_text"])
        if summary_result:
            transcript["summary"] = summary_result["text"]
            transcript["summary_usage"] = {
                "input_tokens": summary_result["input_tokens"],
                "output_tokens": summary_result["output_tokens"],
                "cost": summary_result["cost"],
            }

        save_transcript(transcript)
        return transcript

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e).lower()
        if "invalid credentials" in error_msg or "401" in error_msg:
            raise HTTPException(status_code=401, detail="Your Deepgram API key seems invalid. Check your .env file.")
        if "rate limit" in error_msg or "429" in error_msg:
            raise HTTPException(status_code=429, detail="Too many requests. Wait a moment and try again.")
        if "too short" in error_msg:
            raise HTTPException(status_code=400, detail="The audio file seems too short to transcribe.")
        if "connection" in error_msg or "network" in error_msg or "resolve" in error_msg:
            raise HTTPException(status_code=502, detail="Couldn't reach Deepgram. Check your internet connection.")
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)[:300]}")
    finally:
        # Clean up temp upload file (wav already moved to audio/)
        for p in [upload_path, wav_path]:
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass


@app.get("/api/transcripts")
async def list_transcripts():
    transcripts = []
    for f in sorted(TRANSCRIPTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(f) as fh:
                data = json.load(fh)
                transcripts.append({
                    "id": data["id"],
                    "filename": data["filename"],
                    "created_at": data["created_at"],
                    "duration_seconds": data.get("duration_seconds", 0),
                    "num_speakers": len(data.get("speakers", {})),
                    "summary": data.get("summary", ""),
                    "tags": data.get("tags", []),
                })
        except (json.JSONDecodeError, KeyError):
            continue
    return transcripts


@app.get("/api/tags")
async def get_tags():
    return load_tags()


@app.get("/api/transcripts/copy-by-tag")
async def copy_by_tag(tag: str = Query(..., min_length=1)):
    matching = []
    for f in sorted(TRANSCRIPTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            with open(f) as fh:
                data = json.load(fh)
            if tag in data.get("tags", []):
                matching.append(data)
        except (json.JSONDecodeError, KeyError):
            continue

    if not matching:
        return PlainTextResponse("No transcripts found with this tag.")

    matching.sort(key=lambda t: t.get("created_at", ""))

    lines = [
        f'Below are {len(matching)} meeting transcripts tagged "{tag}". '
        "Please analyze them together and provide:",
        "",
        "1. A summary of each meeting (2-3 sentences each)",
        "2. Common themes and topics across all meetings",
        "3. All action items mentioned, with who is responsible",
        "4. Follow-up items and open questions that still need resolution",
        "5. Key decisions that were made",
        "",
        "=" * 60,
        "",
    ]

    for i, transcript in enumerate(matching, 1):
        lines.append(f"--- MEETING {i} of {len(matching)} ---")
        lines.append("")
        lines.append(generate_copy_text(transcript))
        lines.append("")
        lines.append("=" * 60)
        lines.append("")

    return PlainTextResponse("\n".join(lines))


@app.get("/api/transcripts/{transcript_id}")
async def get_transcript(transcript_id: str):
    return load_transcript(transcript_id)


@app.post("/api/transcripts/{transcript_id}/bookmark")
async def toggle_bookmark(transcript_id: str, request: Request):
    body = await request.json()
    index = body.get("index")
    if index is None or not isinstance(index, int):
        raise HTTPException(status_code=400, detail="Missing or invalid 'index'")
    transcript = load_transcript(transcript_id)
    if index < 0 or index >= len(transcript.get("utterances", [])):
        raise HTTPException(status_code=400, detail="Index out of range")
    bookmarks = transcript.get("bookmarks", [])
    if index in bookmarks:
        bookmarks.remove(index)
    else:
        bookmarks.append(index)
        bookmarks.sort()
    transcript["bookmarks"] = bookmarks
    save_transcript(transcript)
    return {"bookmarks": bookmarks}


@app.post("/api/transcripts/{transcript_id}/summary")
async def generate_transcript_summary(transcript_id: str):
    transcript = load_transcript(transcript_id)
    if not transcript.get("full_text"):
        raise HTTPException(status_code=400, detail="No text to summarize")
    summary_result = await asyncio.to_thread(generate_summary, transcript["full_text"])
    if not summary_result:
        raise HTTPException(status_code=500, detail="Summary generation failed — check your KIMI_API_KEY")
    transcript["summary"] = summary_result["text"]
    transcript["summary_usage"] = {
        "input_tokens": summary_result["input_tokens"],
        "output_tokens": summary_result["output_tokens"],
        "cost": summary_result["cost"],
    }
    path = TRANSCRIPTS_DIR / f"{transcript_id}.json"
    with open(path, "w") as f:
        json.dump(transcript, f, indent=2)
    return {"summary": summary_result["text"]}


@app.delete("/api/transcripts/{transcript_id}")
async def delete_transcript(transcript_id: str):
    path = TRANSCRIPTS_DIR / f"{transcript_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Transcript not found")
    # Also delete audio file
    try:
        with open(path) as f:
            data = json.load(f)
        audio_file = data.get("audio_file")
        if audio_file:
            (AUDIO_DIR / audio_file).unlink(missing_ok=True)
    except Exception:
        pass
    path.unlink()
    return {"ok": True}


@app.get("/api/transcripts/{transcript_id}/audio")
async def get_audio(transcript_id: str):
    transcript = load_transcript(transcript_id)
    audio_file = transcript.get("audio_file")
    if not audio_file:
        raise HTTPException(status_code=404, detail="No audio file for this transcript")
    audio_path = AUDIO_DIR / audio_file
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio file missing")
    return FileResponse(str(audio_path), media_type="audio/wav")


@app.patch("/api/transcripts/{transcript_id}/speakers")
async def rename_speakers(transcript_id: str, request: Request):
    body = await request.json()
    transcript = load_transcript(transcript_id)
    for original, new_name in body.items():
        if original in transcript["speakers"]:
            transcript["speakers"][original] = new_name
    transcript["full_text"] = generate_full_text(transcript["utterances"], transcript["speakers"])
    save_transcript(transcript)
    return transcript


@app.patch("/api/transcripts/{transcript_id}/tags")
async def update_tags(transcript_id: str, request: Request):
    body = await request.json()
    transcript = load_transcript(transcript_id)
    tags_map = load_tags()
    current_tags = transcript.get("tags", [])

    for tag in body.get("add", []):
        tag = tag.strip()
        if not tag or tag in current_tags:
            continue
        current_tags.append(tag)
        if tag not in tags_map:
            used_colors = set(tags_map.values())
            assigned = False
            for color in TAG_COLORS:
                if color not in used_colors:
                    tags_map[tag] = color
                    assigned = True
                    break
            if not assigned:
                tags_map[tag] = TAG_COLORS[len(tags_map) % len(TAG_COLORS)]

    for tag in body.get("remove", []):
        if tag in current_tags:
            current_tags.remove(tag)

    transcript["tags"] = current_tags
    save_transcript(transcript)
    save_tags(tags_map)
    return {"tags": current_tags, "tags_map": tags_map}


@app.get("/api/transcripts/{transcript_id}/copytext")
async def get_copy_text(transcript_id: str):
    transcript = load_transcript(transcript_id)
    text = generate_copy_text(transcript)
    return PlainTextResponse(text)


@app.get("/api/search")
async def search_transcripts(q: str = Query(..., min_length=1)):
    query = q.lower()
    results = []
    for f in sorted(TRANSCRIPTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(f) as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, KeyError):
            continue

        # Search in filename, summary, and utterances
        snippets = []
        filename = data.get("filename", "")
        summary = data.get("summary", "")

        if query in filename.lower():
            snippets.append({"source": "filename", "text": filename})

        if query in summary.lower():
            snippets.append({"source": "summary", "text": summary})

        for u in data.get("utterances", []):
            if query in u.get("text", "").lower():
                speaker = data.get("speakers", {}).get(u["speaker"], u["speaker"])
                snippets.append({
                    "source": "utterance",
                    "text": f"{speaker}: {u['text']}",
                    "start": u.get("start", 0),
                })

        if snippets:
            results.append({
                "id": data["id"],
                "filename": filename,
                "created_at": data.get("created_at", ""),
                "duration_seconds": data.get("duration_seconds", 0),
                "num_speakers": len(data.get("speakers", {})),
                "summary": summary,
                "snippets": snippets[:10],  # Limit snippets per transcript
            })

    return results


@app.get("/api/stats")
async def get_stats():
    now = datetime.now(timezone.utc)
    total_minutes = 0
    month_minutes = 0
    total_files = 0
    month_files = 0
    total_kimi_cost = 0
    month_kimi_cost = 0
    total_kimi_tokens = 0
    month_kimi_tokens = 0

    for f in TRANSCRIPTS_DIR.glob("*.json"):
        try:
            with open(f) as fh:
                data = json.load(fh)
            dur = data.get("duration_seconds", 0) / 60
            total_minutes += dur
            total_files += 1

            kimi_usage = data.get("summary_usage", {})
            kimi_cost = kimi_usage.get("cost", 0)
            kimi_tokens = kimi_usage.get("input_tokens", 0) + kimi_usage.get("output_tokens", 0)
            total_kimi_cost += kimi_cost
            total_kimi_tokens += kimi_tokens

            created = datetime.fromisoformat(data["created_at"])
            if created.year == now.year and created.month == now.month:
                month_minutes += dur
                month_files += 1
                month_kimi_cost += kimi_cost
                month_kimi_tokens += kimi_tokens
        except (json.JSONDecodeError, KeyError):
            continue

    deepgram_total = total_minutes * COST_PER_MINUTE
    deepgram_month = month_minutes * COST_PER_MINUTE
    remaining = max(0, STARTING_CREDIT - deepgram_total)

    total_cost = deepgram_total + total_kimi_cost
    month_cost = deepgram_month + month_kimi_cost

    # Estimate months remaining (Deepgram credit only)
    months_remaining = None
    if deepgram_month > 0:
        months_remaining = round(remaining / deepgram_month, 1)

    return {
        "month_files": month_files,
        "month_minutes": round(month_minutes, 1),
        "month_cost": round(month_cost, 4),
        "month_deepgram_cost": round(deepgram_month, 4),
        "month_kimi_cost": round(month_kimi_cost, 4),
        "month_kimi_tokens": month_kimi_tokens,
        "total_files": total_files,
        "total_minutes": round(total_minutes, 1),
        "total_cost": round(total_cost, 4),
        "total_deepgram_cost": round(deepgram_total, 4),
        "total_kimi_cost": round(total_kimi_cost, 4),
        "total_kimi_tokens": total_kimi_tokens,
        "credit_remaining": round(remaining, 2),
        "months_remaining": months_remaining,
    }


@app.get("/api/stats/per-file")
async def get_stats_per_file():
    files = []
    for f in sorted(TRANSCRIPTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(f) as fh:
                data = json.load(fh)
            dur_min = data.get("duration_seconds", 0) / 60
            kimi_cost = data.get("summary_usage", {}).get("cost", 0)
            files.append({
                "id": data["id"],
                "filename": data["filename"],
                "created_at": data["created_at"],
                "duration_minutes": round(dur_min, 1),
                "estimated_cost": round(dur_min * COST_PER_MINUTE, 4),
                "kimi_cost": round(kimi_cost, 4),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return files


# Serve static files and SPA fallback
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """Serve index.html for all non-API routes (SPA)."""
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
