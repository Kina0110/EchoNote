from datetime import datetime


def format_timestamp(seconds: float) -> str:
    """Format seconds as [MM:SS] or [H:MM:SS]."""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"[{h}:{m:02d}:{s:02d}]"
    return f"[{m:02d}:{s:02d}]"


def merge_short_utterances(utterances: list, max_gap: float = 15.0) -> list:
    """Merge consecutive same-speaker utterances within a time gap."""
    if not utterances:
        return utterances
    merged = [dict(utterances[0])]
    for u in utterances[1:]:
        if u.get("type") == "file-boundary":
            merged.append(dict(u))
            continue
        prev = merged[-1]
        if prev.get("type") == "file-boundary":
            merged.append(dict(u))
            continue
        same_speaker = u["speaker"] == prev["speaker"]
        gap = u["start"] - prev["end"]
        if same_speaker and gap <= max_gap:
            prev["text"] = prev["text"].rstrip(",. ") + " " + u["text"]
            prev["end"] = u["end"]
        else:
            merged.append(dict(u))
    return merged


def generate_full_text(utterances: list, speakers: dict) -> str:
    """Build the full_text field from utterances with speaker name mapping."""
    lines = []
    for u in utterances:
        if u.get("type") == "file-boundary":
            continue
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
        if u.get("type") == "file-boundary":
            lines.append(f"\n--- {u['filename']} ---\n")
            continue
        display_name = speakers.get(u["speaker"], u["speaker"])
        ts = format_timestamp(u["start"])
        lines.append(f"{ts} {display_name}: {u['text']}")

    return "\n".join(lines)
