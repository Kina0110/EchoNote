import hashlib
import json
import math
import os
import threading
from datetime import datetime, timezone

from openai import OpenAI

from config import EMBEDDINGS_FILE, EMBED_COST_PER_TOKEN
from storage import iter_transcripts

CHUNK_WORDS = 500
OVERLAP_WORDS = 50
EMBED_MODEL = "text-embedding-3-small"
KEYWORD_BOOST = 1.25

_lock = threading.Lock()


# --- Chunking ---

def chunk_text(text: str) -> list[dict]:
    """Split text into overlapping word-window chunks."""
    words = text.split()
    step = CHUNK_WORDS - OVERLAP_WORDS
    chunks = []
    for start in range(0, len(words), step):
        chunk_words = words[start:start + CHUNK_WORDS]
        if len(chunk_words) < 30:  # skip very short trailing chunks
            break
        chunks.append({"text": " ".join(chunk_words), "word_start": start})
    return chunks


# --- Cosine similarity (pure Python, no numpy) ---

def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# --- Index I/O ---

def load_embeddings_index() -> dict:
    try:
        with open(EMBEDDINGS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_embeddings_index(index: dict) -> None:
    tmp = EMBEDDINGS_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(index, f)
    tmp.replace(EMBEDDINGS_FILE)


# --- OpenAI embedding API ---

def embed_texts(texts: list[str]) -> list[list[float]] | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or not texts:
        return None
    try:
        client = OpenAI(api_key=api_key)
        results = []
        # Batch in groups of 100
        for i in range(0, len(texts), 100):
            batch = texts[i:i + 100]
            resp = client.embeddings.create(model=EMBED_MODEL, input=batch)
            results.extend([item.embedding for item in resp.data])
        return results
    except Exception:
        return None


# --- Text hash for staleness detection ---

def _text_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


# --- Embed a single transcript ---

def embed_transcript(transcript: dict) -> bool:
    """Chunk and embed a transcript's full_text. Returns True on success."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return False

    full_text = transcript.get("full_text", "").strip()
    if not full_text:
        return False

    tid = transcript["id"]
    text_hash = _text_hash(full_text)

    with _lock:
        index = load_embeddings_index()
        existing = index.get(tid, {})
        if existing.get("text_hash") == text_hash:
            return True  # Already up to date

    chunks = chunk_text(full_text)
    if not chunks:
        return False

    vectors = embed_texts([c["text"] for c in chunks])
    if not vectors or len(vectors) != len(chunks):
        return False

    entry = {
        "embedded_at": datetime.now(timezone.utc).isoformat(),
        "text_hash": text_hash,
        "chunks": [
            {"text": c["text"], "word_start": c["word_start"], "embedding": v}
            for c, v in zip(chunks, vectors)
        ],
    }

    with _lock:
        index = load_embeddings_index()
        index[tid] = entry
        save_embeddings_index(index)

    return True


# --- Semantic search ---

def semantic_search(
    query_embedding: list[float],
    index: dict,
    transcript_meta: dict,  # {id: {filename, ...}}
    keyword_ids: set[str] | None = None,
    top_k: int = 10,
) -> list[dict]:
    """Score all chunks against the query embedding. Returns top_k results."""
    hits = []
    for tid, entry in index.items():
        for chunk in entry.get("chunks", []):
            emb = chunk.get("embedding")
            if not emb:
                continue
            score = cosine_similarity(query_embedding, emb)
            # Boost chunks from keyword-matched transcripts
            if keyword_ids and tid in keyword_ids:
                score *= KEYWORD_BOOST
            hits.append({
                "transcript_id": tid,
                "filename": transcript_meta.get(tid, {}).get("filename", "Unknown"),
                "chunk_text": chunk["text"],
                "score": score,
            })

    hits.sort(key=lambda x: x["score"], reverse=True)
    # Deduplicate: keep only best chunk per transcript
    seen = set()
    deduped = []
    for h in hits:
        if h["transcript_id"] not in seen:
            seen.add(h["transcript_id"])
            deduped.append(h)
        if len(deduped) >= top_k:
            break
    return deduped


# --- Build all embeddings ---

def build_all_embeddings() -> dict:
    """Embed all transcripts. Returns stats."""
    processed = skipped = failed = 0
    for transcript in iter_transcripts():
        text = transcript.get("full_text", "").strip()
        if not text:
            skipped += 1
            continue
        text_hash = _text_hash(text)
        with _lock:
            index = load_embeddings_index()
            existing = index.get(transcript["id"], {})
        if existing.get("text_hash") == text_hash:
            skipped += 1
            continue
        ok = embed_transcript(transcript)
        if ok:
            processed += 1
        else:
            failed += 1
    return {"processed": processed, "skipped": skipped, "failed": failed}
