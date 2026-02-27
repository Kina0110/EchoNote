import json
import subprocess
import tempfile
from pathlib import Path


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
