from pathlib import Path

import numpy as np
import soundfile as sf
from resemblyzer import VoiceEncoder
from scipy.spatial.distance import cosine

from storage import load_voiceprints

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
            if u["speaker"] == speaker_key:
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


def match_speakers_to_voiceprints(audio_path: Path, utterances: list, speakers: dict) -> dict:
    """Try to match diarized speakers to known voiceprints. Returns updated speakers dict."""
    voiceprints = load_voiceprints()
    if not voiceprints:
        return speakers

    updated = dict(speakers)

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
        if similarity >= 0.75:
            updated[speaker_key] = name
            used_keys.add(speaker_key)
            used_names.add(name)

    return updated
