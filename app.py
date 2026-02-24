import asyncio
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
from deepgram import DeepgramClient
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

from ai import generate_summary
from audio import extract_audio
from config import (
    ALLOWED_EXTENSIONS, AUDIO_DIR, COST_PER_MINUTE, MAX_FILE_SIZE,
    STARTING_CREDIT, STATIC_DIR, TAG_COLORS, TRANSCRIPTS_DIR, UPLOADS_DIR,
    VIDEO_EXTENSIONS, VIDEO_MEDIA_TYPES, VIDEOS_DIR,
    api_key_configured, ffmpeg_available,
)
from helpers import generate_copy_text, generate_full_text, merge_short_utterances
from storage import (
    load_tags, load_transcript, load_voiceprints,
    save_tags, save_transcript, save_voiceprints,
)
from voiceprints import extract_speaker_embedding, match_speakers_to_voiceprints

app = FastAPI(title="Voice Transcriber")


# --- Health ---

@app.get("/api/health")
async def health():
    return {"ffmpeg": ffmpeg_available, "api_key": api_key_configured}


# --- Transcription ---

@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...), keep_video: bool = Form(False)):
    if not ffmpeg_available:
        raise HTTPException(status_code=500, detail="ffmpeg is not installed. Please install it: brew install ffmpeg")
    if not api_key_configured:
        raise HTTPException(status_code=500, detail="DEEPGRAM_API_KEY not set. Copy .env.example to .env and add your key.")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{ext}'. Supported: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    file_id = uuid.uuid4().hex
    upload_path = UPLOADS_DIR / f"{file_id}{ext}"
    wav_path = UPLOADS_DIR / f"{file_id}.wav"

    try:
        async with aiofiles.open(upload_path, "wb") as out:
            total = 0
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_FILE_SIZE:
                    raise HTTPException(status_code=413, detail="File too large. Maximum size is 2GB.")
                await out.write(chunk)

        await asyncio.to_thread(extract_audio, upload_path, wav_path)

        dg_client = DeepgramClient(api_key=os.getenv("DEEPGRAM_API_KEY"))
        with open(wav_path, "rb") as audio_file:
            audio_data = audio_file.read()

        response = await asyncio.to_thread(
            dg_client.listen.v1.media.transcribe_file,
            request=audio_data,
            model="nova-3",
            diarize=True, punctuate=True, paragraphs=True,
            utterances=True, smart_format=True,
        )

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

        transcript_id = str(uuid.uuid4())
        audio_filename = f"{transcript_id}.wav"
        audio_dest = AUDIO_DIR / audio_filename
        shutil.move(str(wav_path), str(audio_dest))

        # Auto-match speakers to known voiceprints
        try:
            speakers = await asyncio.to_thread(
                match_speakers_to_voiceprints, audio_dest, utterances, speakers
            )
        except Exception:
            pass

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

        if keep_video and ext in VIDEO_EXTENSIONS and upload_path.exists():
            video_filename = f"{transcript_id}{ext}"
            shutil.copy2(str(upload_path), str(VIDEOS_DIR / video_filename))
            transcript["video_file"] = video_filename

        # Generate summary + action items
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
        for p in [upload_path, wav_path]:
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass


# --- Transcript CRUD ---

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


@app.get("/api/transcripts/{transcript_id}")
async def get_transcript(transcript_id: str):
    return load_transcript(transcript_id)


@app.delete("/api/transcripts/{transcript_id}")
async def delete_transcript(transcript_id: str):
    path = TRANSCRIPTS_DIR / f"{transcript_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Transcript not found")
    try:
        with open(path) as f:
            data = json.load(f)
        audio_file = data.get("audio_file")
        if audio_file:
            (AUDIO_DIR / audio_file).unlink(missing_ok=True)
        video_file = data.get("video_file")
        if video_file:
            (VIDEOS_DIR / video_file).unlink(missing_ok=True)
    except Exception:
        pass
    path.unlink()
    return {"ok": True}


# --- Bookmarks ---

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


# --- Summary & Action Items ---

@app.post("/api/transcripts/{transcript_id}/summary")
async def generate_transcript_summary(transcript_id: str):
    transcript = load_transcript(transcript_id)
    if not transcript.get("full_text"):
        raise HTTPException(status_code=400, detail="No text to summarize")
    summary_result = await asyncio.to_thread(generate_summary, transcript["full_text"])
    if not summary_result:
        raise HTTPException(status_code=500, detail="Summary generation failed — check your OPENAI_API_KEY")
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
    save_transcript(transcript)
    return {"summary": summary_result["text"], "action_items": transcript["action_items"]}


@app.patch("/api/transcripts/{transcript_id}/action-items")
async def update_action_item(transcript_id: str, request: Request):
    body = await request.json()
    index = body.get("index")
    status = body.get("status")
    if index is None or status not in ("pending", "accepted", "dismissed", "deleted"):
        raise HTTPException(status_code=400, detail="Provide index and status (pending|accepted|dismissed|deleted)")
    transcript = load_transcript(transcript_id)
    items = transcript.get("action_items", [])
    if index < 0 or index >= len(items):
        raise HTTPException(status_code=400, detail="Index out of range")
    if status == "deleted":
        items.pop(index)
    else:
        items[index]["status"] = status
    transcript["action_items"] = items
    save_transcript(transcript)
    return {"action_items": items}


# --- Speakers ---

@app.patch("/api/transcripts/{transcript_id}/speakers")
async def rename_speakers(transcript_id: str, request: Request):
    body = await request.json()
    transcript = load_transcript(transcript_id)
    for original, new_name in body.items():
        if original in transcript["speakers"]:
            transcript["speakers"][original] = new_name
    transcript["full_text"] = generate_full_text(transcript["utterances"], transcript["speakers"])
    save_transcript(transcript)

    # Save voiceprints for renamed speakers (in background)
    audio_file = transcript.get("audio_file")
    if audio_file:
        audio_path = AUDIO_DIR / audio_file
        if audio_path.exists():
            async def _save_voiceprints():
                try:
                    voiceprints = load_voiceprints()
                    for orig, name in body.items():
                        if re.match(r"^Speaker \d+$", name):
                            continue
                        embedding = await asyncio.to_thread(
                            extract_speaker_embedding, audio_path, transcript["utterances"], orig
                        )
                        if embedding:
                            voiceprints[name] = embedding
                    save_voiceprints(voiceprints)
                except Exception:
                    pass
            asyncio.create_task(_save_voiceprints())

    return transcript


# --- Tags ---

@app.get("/api/tags")
async def get_tags():
    return load_tags()


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


# --- Media ---

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


@app.get("/api/transcripts/{transcript_id}/video")
async def get_video(transcript_id: str):
    transcript = load_transcript(transcript_id)
    video_file = transcript.get("video_file")
    if not video_file:
        raise HTTPException(status_code=404, detail="No video file for this transcript")
    video_path = VIDEOS_DIR / video_file
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file missing")
    ext = Path(video_file).suffix.lower()
    media_type = VIDEO_MEDIA_TYPES.get(ext, "video/mp4")
    return FileResponse(str(video_path), media_type=media_type)


# --- Copy & Export ---

@app.get("/api/transcripts/{transcript_id}/copytext")
async def get_copy_text(transcript_id: str):
    transcript = load_transcript(transcript_id)
    text = generate_copy_text(transcript)
    return PlainTextResponse(text)


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


# --- Search ---

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
                "snippets": snippets[:10],
            })

    return results


# --- Stats ---

@app.get("/api/stats")
async def get_stats():
    now = datetime.now(timezone.utc)
    total_minutes = 0
    month_minutes = 0
    total_files = 0
    month_files = 0
    total_gpt_cost = 0
    month_gpt_cost = 0
    total_gpt_tokens = 0
    month_gpt_tokens = 0

    for f in TRANSCRIPTS_DIR.glob("*.json"):
        try:
            with open(f) as fh:
                data = json.load(fh)
            dur = data.get("duration_seconds", 0) / 60
            total_minutes += dur
            total_files += 1

            gpt_usage = data.get("summary_usage", {})
            gpt_cost = gpt_usage.get("cost", 0)
            gpt_tokens = gpt_usage.get("input_tokens", 0) + gpt_usage.get("output_tokens", 0)
            total_gpt_cost += gpt_cost
            total_gpt_tokens += gpt_tokens

            created = datetime.fromisoformat(data["created_at"])
            if created.year == now.year and created.month == now.month:
                month_minutes += dur
                month_files += 1
                month_gpt_cost += gpt_cost
                month_gpt_tokens += gpt_tokens
        except (json.JSONDecodeError, KeyError):
            continue

    deepgram_total = total_minutes * COST_PER_MINUTE
    deepgram_month = month_minutes * COST_PER_MINUTE
    remaining = max(0, STARTING_CREDIT - deepgram_total)
    total_cost = deepgram_total + total_gpt_cost
    month_cost = deepgram_month + month_gpt_cost

    months_remaining = None
    if deepgram_month > 0:
        months_remaining = round(remaining / deepgram_month, 1)

    video_storage_bytes = sum(f.stat().st_size for f in VIDEOS_DIR.iterdir() if f.is_file())
    video_storage_mb = round(video_storage_bytes / (1024 * 1024), 1)

    return {
        "month_files": month_files,
        "month_minutes": round(month_minutes, 1),
        "month_cost": round(month_cost, 4),
        "month_deepgram_cost": round(deepgram_month, 4),
        "month_gpt_cost": round(month_gpt_cost, 4),
        "month_gpt_tokens": month_gpt_tokens,
        "total_files": total_files,
        "total_minutes": round(total_minutes, 1),
        "total_cost": round(total_cost, 4),
        "total_deepgram_cost": round(deepgram_total, 4),
        "total_gpt_cost": round(total_gpt_cost, 4),
        "total_gpt_tokens": total_gpt_tokens,
        "credit_remaining": round(remaining, 2),
        "months_remaining": months_remaining,
        "video_storage_mb": video_storage_mb,
    }


@app.get("/api/stats/per-file")
async def get_stats_per_file():
    files = []
    for f in sorted(TRANSCRIPTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(f) as fh:
                data = json.load(fh)
            dur_min = data.get("duration_seconds", 0) / 60
            gpt_cost = data.get("summary_usage", {}).get("cost", 0)
            files.append({
                "id": data["id"],
                "filename": data["filename"],
                "created_at": data["created_at"],
                "duration_minutes": round(dur_min, 1),
                "estimated_cost": round(dur_min * COST_PER_MINUTE, 4),
                "gpt_cost": round(gpt_cost, 4),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return files


# --- Static files & SPA ---

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
