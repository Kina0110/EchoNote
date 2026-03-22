import json
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime, timezone


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


def get_duration(file_path: Path) -> float:
    """Get duration of an audio/video file in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(file_path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.decode(errors='replace')[:500]}")
    info = json.loads(result.stdout)
    return float(info["format"]["duration"])


def get_file_creation_date(file_path: Path) -> str | None:
    """Extract original creation date from media file metadata using ffprobe.
    Returns ISO format string or None if not available."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(file_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        if result.returncode != 0:
            return None
        info = json.loads(result.stdout)
        tags = info.get("format", {}).get("tags", {})
        # Try common metadata keys for creation date
        for key in ("creation_time", "date", "DATE", "ICRD", "TDRC"):
            if key in tags:
                try:
                    dt = datetime.fromisoformat(tags[key].replace("Z", "+00:00"))
                    return dt.isoformat()
                except (ValueError, TypeError):
                    # Try just the date portion
                    try:
                        dt = datetime.strptime(tags[key][:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                        return dt.isoformat()
                    except (ValueError, TypeError):
                        continue
    except Exception:
        pass
    # Fall back to filesystem modification time
    try:
        stat = file_path.stat()
        # On macOS, st_birthtime is the actual creation time
        ctime = getattr(stat, "st_birthtime", stat.st_mtime)
        return datetime.fromtimestamp(ctime, tz=timezone.utc).isoformat()
    except Exception:
        return None


def concat_audio(wav_paths: list[Path], output_path: Path) -> None:
    """Concatenate multiple WAV files into one using ffmpeg concat demuxer."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for p in wav_paths:
            f.write(f"file '{p}'\n")
        concat_list = f.name

    try:
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list,
            "-c", "copy",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {result.stderr.decode(errors='replace')[:500]}")
    finally:
        Path(concat_list).unlink(missing_ok=True)


def concat_video(video_paths: list[Path], output_path: Path) -> None:
    """Concatenate multiple video files into one, re-encoding for compatibility."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        for p in video_paths:
            f.write(f"file '{p}'\n")
        concat_list = f.name

    try:
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list,
            "-c:v", "libx264",
            "-c:a", "aac",
            "-movflags", "+faststart",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=1200)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg video concat failed: {result.stderr.decode(errors='replace')[:500]}")
    finally:
        Path(concat_list).unlink(missing_ok=True)
