from pathlib import Path

import numpy as np
import soundfile as sf
from resemblyzer import VoiceEncoder
from scipy.spatial.distance import cosine

from storage import load_voiceprints

# Weight given to the existing voiceprint vs a new sample (0–1).
# 0.7 = new samples nudge the profile gently; outliers barely move it.
_BLEND_WEIGHT = 0.7

# Lazy-load encoder on first use (model download ~50MB on first run)
_voice_encoder = None


def get_voice_encoder():
    global _voice_encoder
    if _voice_encoder is None:
        _voice_encoder = VoiceEncoder()
    return _voice_encoder


def extract_speaker_embedding(audio_path: Path, utterances: list, speaker_key: str) -> list | None:
    """Extract a voice embedding for a speaker from their utterances in an audio file."""
    try:
        audio, sr = sf.read(audio_path)
        # Collect all audio segments for this speaker
        segments = []
        for u in utterances:
            if u.get("type") == "file-boundary":
                continue
            if u.get("speaker") == speaker_key:
                start_sample = int(u["start"] * sr)
                end_sample = int(u["end"] * sr)
                if end_sample > start_sample:
                    segments.append(audio[start_sample:end_sample])
        if not segments:
            return None
        # Concatenate all segments (more audio = better embedding)
        combined = np.concatenate(segments)
        # Need at least 1 second of audio for a reliable embedding
        if len(combined) < sr:
            return None
        encoder = get_voice_encoder()
        embedding = encoder.embed_utterance(combined)
        return embedding.tolist()
    except Exception:
        return None


def blend_embeddings(existing: list, new: list, weight: float = _BLEND_WEIGHT) -> list:
    """Weighted blend: weight * existing + (1-weight) * new, then L2-normalize."""
    a = np.array(existing)
    b = np.array(new)
    blended = weight * a + (1 - weight) * b
    norm = np.linalg.norm(blended)
    if norm > 0:
        blended /= norm
    return blended.tolist()


def merge_speaker_embeddings(embeddings: list[list]) -> list:
    """Average multiple embeddings (e.g. two speakers named the same), then L2-normalize."""
    arr = np.mean([np.array(e) for e in embeddings], axis=0)
    norm = np.linalg.norm(arr)
    if norm > 0:
        arr /= norm
    return arr.tolist()


def match_speakers_to_voiceprints(audio_path: Path, utterances: list, speakers: dict) -> tuple[dict, dict]:
    """Try to match diarized speakers to known voiceprints.
    Returns (updated_speakers, matched_map) where matched_map only contains auto-matched entries."""
    voiceprints = load_voiceprints()
    if not voiceprints:
        return dict(speakers), {}

    updated = dict(speakers)
    matched = {}

    # Score each speaker against all voiceprints
    candidates = []
    for speaker_key in speakers:
        embedding = extract_speaker_embedding(audio_path, utterances, speaker_key)
        if embedding is None:
            continue
        for name, known_emb in voiceprints.items():
            similarity = 1 - cosine(embedding, known_emb)
            candidates.append((similarity, speaker_key, name))

    # Sort by similarity descending, greedily assign best matches
    candidates.sort(key=lambda x: x[0], reverse=True)
    used_keys = set()
    used_names = set()
    for similarity, speaker_key, name in candidates:
        if speaker_key in used_keys or name in used_names:
            continue
        if similarity >= 0.90:
            updated[speaker_key] = name
            matched[speaker_key] = name
            used_keys.add(speaker_key)
            used_names.add(name)

    return updated, matched
