import asyncio
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import FileResponse

from audio import concat_audio, concat_video, extract_audio, get_duration
from config import (
    ALLOWED_EXTENSIONS, AUDIO_DIR, COST_PER_MINUTE, MAX_FILE_SIZE,
    STARTING_CREDIT, STATIC_DIR, TAG_COLORS, TRANSCRIPTS_DIR, UPLOADS_DIR,
    VIDEO_EXTENSIONS, VIDEO_MEDIA_TYPES, VIDEOS_DIR,
    api_key_configured, ffmpeg_available,
)
from helpers import generate_copy_text, generate_full_text, merge_short_utterances
from storage import (
    iter_transcripts, load_settings, load_tags, load_transcript, load_voiceprints,
    save_settings, save_tags, save_transcript, save_voiceprints,
)
from transcription import (
    attach_chapters, attach_summary, cleanup_files, extract_duration, extract_utterances,
    handle_transcription_error, save_upload, transcribe_audio,
)
from voiceprints import extract_speaker_embedding, match_speakers_to_voiceprints

app = FastAPI(title="Transcribbly")


def _check_prerequisites():
    if not ffmpeg_available:
        raise HTTPException(status_code=500, detail="ffmpeg is not installed. Please install it: brew install ffmpeg")
    if not api_key_configured:
        raise HTTPException(status_code=500, detail="DEEPGRAM_API_KEY not set. Copy .env.example to .env and add your key.")


async def _match_voiceprints(audio_path, utterances, speakers):
    try:
        return await asyncio.to_thread(
            match_speakers_to_voiceprints, audio_path, utterances, speakers
        )
    except Exception:
        return speakers


# --- Health ---

@app.get("/api/health")
async def health():
    return {"ffmpeg": ffmpeg_available, "api_key": api_key_configured}


# --- Transcription ---

@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...), keep_video: bool = Form(False)):
    _check_prerequisites()

    upload_path, ext = await save_upload(file, UPLOADS_DIR)
    wav_path = upload_path.with_suffix(".wav")

    try:
        await asyncio.to_thread(extract_audio, upload_path, wav_path)
        result = await transcribe_audio(wav_path)

        duration = extract_duration(result)
        utterances, speakers = extract_utterances(result)

        transcript_id = str(uuid.uuid4())
        audio_filename = f"{transcript_id}.wav"
        audio_dest = AUDIO_DIR / audio_filename
        shutil.move(str(wav_path), str(audio_dest))

        speakers = await _match_voiceprints(audio_dest, utterances, speakers)

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

        await attach_summary(transcript)
        await attach_chapters(transcript)
        save_transcript(transcript)
        return transcript

    except HTTPException:
        raise
    except Exception as e:
        raise handle_transcription_error(e)
    finally:
        cleanup_files(upload_path, wav_path)


# --- Multi-file Transcription ---

@app.post("/api/transcribe-multi")
async def transcribe_multi(files: List[UploadFile] = File(...), keep_video: bool = Form(False)):
    _check_prerequisites()
    if len(files) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 files to combine.")

    upload_paths = []
    wav_paths = []
    filenames = []
    combined_wav = None

    try:
        # Save and extract audio from each file
        for f in files:
            upload_path, ext = await save_upload(f, UPLOADS_DIR)
            wav_path = upload_path.with_suffix(".wav")
            await asyncio.to_thread(extract_audio, upload_path, wav_path)
            upload_paths.append(upload_path)
            wav_paths.append(wav_path)
            filenames.append(f.filename or "unknown")

        # Get duration of each WAV to know file boundaries
        durations = []
        for wp in wav_paths:
            durations.append(await asyncio.to_thread(get_duration, wp))

        # Concatenate all WAVs and transcribe
        combined_wav = UPLOADS_DIR / f"{uuid.uuid4().hex}_combined.wav"
        await asyncio.to_thread(concat_audio, wav_paths, combined_wav)
        result = await transcribe_audio(combined_wav)

        duration = extract_duration(result)
        utterances, speakers = extract_utterances(result)

        # Insert file-boundary markers between files
        offset = 0
        boundaries = []
        for i, dur in enumerate(durations):
            offset += dur
            if i < len(durations) - 1:
                boundaries.append({"start": round(offset, 2), "filename": filenames[i + 1]})

        for boundary in reversed(boundaries):
            insert_idx = len(utterances)
            for j, u in enumerate(utterances):
                if u.get("start", 0) >= boundary["start"]:
                    insert_idx = j
                    break
            utterances.insert(insert_idx, {
                "type": "file-boundary",
                "filename": boundary["filename"],
                "start": boundary["start"],
            })

        transcript_id = str(uuid.uuid4())
        audio_filename = f"{transcript_id}.wav"
        audio_dest = AUDIO_DIR / audio_filename
        shutil.move(str(combined_wav), str(audio_dest))

        speakers = await _match_voiceprints(audio_dest, utterances, speakers)

        transcript = {
            "id": transcript_id,
            "filename": " + ".join(filenames),
            "source_files": filenames,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "duration_seconds": round(duration, 2),
            "speakers": speakers,
            "utterances": utterances,
            "full_text": generate_full_text(utterances, speakers),
            "audio_file": audio_filename,
        }

        # Concatenate videos if requested
        if keep_video:
            video_uploads = [p for p in upload_paths if p.suffix.lower() in VIDEO_EXTENSIONS]
            if len(video_uploads) >= 2:
                video_filename = f"{transcript_id}.mp4"
                await asyncio.to_thread(concat_video, video_uploads, VIDEOS_DIR / video_filename)
                transcript["video_file"] = video_filename
            elif len(video_uploads) == 1:
                video_filename = f"{transcript_id}{video_uploads[0].suffix}"
                shutil.copy2(str(video_uploads[0]), str(VIDEOS_DIR / video_filename))
                transcript["video_file"] = video_filename

        await attach_summary(transcript)
        save_transcript(transcript)
        return transcript

    except HTTPException:
        raise
    except Exception as e:
        raise handle_transcription_error(e)
    finally:
        cleanup_files(*upload_paths, *wav_paths)
        if combined_wav:
            cleanup_files(combined_wav)


# --- Transcript CRUD ---

@app.get("/api/transcripts")
async def list_transcripts():
    transcripts = [
        {
            "id": data["id"],
            "filename": data["filename"],
            "created_at": data["created_at"],
            "duration_seconds": data.get("duration_seconds", 0),
            "num_speakers": len(data.get("speakers", {})),
            "summary": data.get("summary", ""),
            "tags": data.get("tags", []),
        }
        for data in iter_transcripts()
    ]
    transcripts.sort(key=lambda t: t["created_at"], reverse=True)
    return transcripts


@app.get("/api/transcripts/{transcript_id}")
async def get_transcript(transcript_id: str):
    transcript = load_transcript(transcript_id)
    transcript["utterances"] = merge_short_utterances(transcript.get("utterances", []))
    return transcript


@app.patch("/api/transcripts/{transcript_id}/rename")
async def rename_transcript(transcript_id: str, request: Request):
    body = await request.json()
    name = body.get("filename", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Filename cannot be empty")
    transcript = load_transcript(transcript_id)
    transcript["filename"] = name
    save_transcript(transcript)
    return {"filename": name}


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


# --- Comments ---

@app.post("/api/transcripts/{transcript_id}/comments")
async def add_comment(transcript_id: str, request: Request):
    body = await request.json()
    index = body.get("index")
    text = body.get("text", "").strip()
    if index is None or not isinstance(index, int):
        raise HTTPException(status_code=400, detail="Missing or invalid 'index'")
    if not text:
        raise HTTPException(status_code=400, detail="Comment text cannot be empty")
    transcript = load_transcript(transcript_id)
    if index < 0 or index >= len(transcript.get("utterances", [])):
        raise HTTPException(status_code=400, detail="Index out of range")
    comments = transcript.get("comments", {})
    key = str(index)
    if key not in comments:
        comments[key] = []
    comments[key].append({
        "id": str(uuid.uuid4())[:8],
        "text": text,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    transcript["comments"] = comments
    save_transcript(transcript)
    return {"comments": comments}


@app.delete("/api/transcripts/{transcript_id}/comments")
async def delete_comment(transcript_id: str, request: Request):
    body = await request.json()
    index = body.get("index")
    comment_id = body.get("comment_id")
    if index is None or not isinstance(index, int) or not comment_id:
        raise HTTPException(status_code=400, detail="Provide 'index' and 'comment_id'")
    transcript = load_transcript(transcript_id)
    comments = transcript.get("comments", {})
    key = str(index)
    if key not in comments:
        raise HTTPException(status_code=404, detail="No comments at this index")
    original_len = len(comments[key])
    comments[key] = [c for c in comments[key] if c["id"] != comment_id]
    if len(comments[key]) == original_len:
        raise HTTPException(status_code=404, detail="Comment not found")
    if not comments[key]:
        del comments[key]
    transcript["comments"] = comments
    save_transcript(transcript)
    return {"comments": comments}


# --- Summary & Action Items ---

@app.post("/api/transcripts/{transcript_id}/summary")
async def generate_transcript_summary(transcript_id: str):
    transcript = load_transcript(transcript_id)
    if not transcript.get("full_text"):
        raise HTTPException(status_code=400, detail="No text to summarize")
    await attach_summary(transcript)
    if not transcript.get("summary"):
        raise HTTPException(status_code=500, detail="Summary generation failed — check your OPENAI_API_KEY")
    save_transcript(transcript)
    return {"summary": transcript["summary"], "action_items": transcript.get("action_items", [])}


@app.post("/api/transcripts/{transcript_id}/chapters")
async def generate_transcript_chapters(transcript_id: str):
    transcript = load_transcript(transcript_id)
    if not transcript.get("utterances"):
        raise HTTPException(status_code=400, detail="No utterances to analyze")
    try:
        await attach_chapters(transcript)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chapter generation failed: {str(e)[:300]}")
    if not transcript.get("chapters"):
        raise HTTPException(status_code=500, detail="Chapter generation failed — transcript may be too short or OpenAI key missing")
    save_transcript(transcript)
    return {"chapters": transcript["chapters"]}


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


# --- Settings ---

@app.get("/api/settings")
async def get_settings():
    return load_settings()


@app.patch("/api/settings")
async def update_settings(request: Request):
    body = await request.json()
    settings = load_settings()
    if "profile" in body:
        profile = body["profile"]
        settings["profile"] = {
            "name": str(profile.get("name", "")).strip(),
            "role": str(profile.get("role", "")).strip(),
        }
    save_settings(settings)
    return settings


# --- Re-transcribe (regenerate word timestamps) ---

@app.post("/api/transcripts/{transcript_id}/retranscribe")
async def retranscribe(transcript_id: str):
    """Re-transcribe an existing transcript's audio to get word-level timestamps."""
    transcript = load_transcript(transcript_id)
    audio_file = transcript.get("audio_file")
    if not audio_file:
        raise HTTPException(status_code=400, detail="No audio file available for re-transcription")
    audio_path = AUDIO_DIR / audio_file
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio file missing from disk")

    try:
        result = await transcribe_audio(audio_path)
    except Exception as e:
        raise handle_transcription_error(e)

    utterances, speakers = extract_utterances(result)

    # Preserve existing speaker renames
    old_speakers = transcript.get("speakers", {})
    for key in speakers:
        if key in old_speakers and old_speakers[key] != key:
            speakers[key] = old_speakers[key]

    transcript["utterances"] = utterances
    transcript["speakers"] = speakers
    transcript["full_text"] = generate_full_text(utterances, speakers)
    save_transcript(transcript)
    return transcript


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

@app.get("/api/transcripts/{transcript_id}/download")
async def download_zip(transcript_id: str):
    """Download transcript as a ZIP containing TXT, SRT, JSON, and audio."""
    import io
    import zipfile
    from starlette.responses import Response
    from helpers import format_timestamp

    transcript = load_transcript(transcript_id)
    base_name = Path(transcript["filename"]).stem

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        txt = generate_copy_text(transcript)
        zf.writestr(f"{base_name}.txt", txt)

    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{base_name}.zip"'},
    )


def _to_srt_time(seconds: float) -> str:
    """Convert seconds to SRT timestamp format HH:MM:SS,mmm."""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


@app.get("/api/transcripts/{transcript_id}/copytext")
async def get_copy_text(transcript_id: str):
    transcript = load_transcript(transcript_id)
    text = generate_copy_text(transcript)
    return PlainTextResponse(text)


@app.get("/api/transcripts/copy-by-tag")
async def copy_by_tag(tag: str = Query(..., min_length=1)):
    matching = [data for data in iter_transcripts() if tag in data.get("tags", [])]

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
    for data in iter_transcripts(sort_key="mtime", reverse=True):
        snippets = []
        filename = data.get("filename", "")
        summary = data.get("summary", "")

        if query in filename.lower():
            snippets.append({"source": "filename", "text": filename})
        if query in summary.lower():
            snippets.append({"source": "summary", "text": summary})

        for u in data.get("utterances", []):
            if u.get("type") == "file-boundary":
                continue
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

    for data in iter_transcripts():
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
    for data in iter_transcripts(sort_key="mtime", reverse=True):
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
    return files


# --- Static files & SPA ---

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
