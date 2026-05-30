"""ChromaDB vector store wrapper with DB25 hybrid search.

DB25 = Dense (Chroma cosine ANN) + BM25 keyword scoring,
fused via Reciprocal Rank Fusion (RRF, k=60).
"""
from __future__ import annotations
import sys, os, uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from pipeline.security import encrypt_data, decrypt_data

# ── ChromaDB client ───────────────────────────────────────────────────────────
import chromadb
from chromadb.config import Settings

# ── BM25 (for DB25 hybrid search) ────────────────────────────────────────────
from rank_bm25 import BM25Okapi

_client: chromadb.PersistentClient | None = None
_collection: chromadb.Collection | None = None


def _get_collection() -> chromadb.Collection:
    global _client, _collection
    if _collection is None:
        persist_dir = config.CHROMA_PERSIST_DIR
        os.makedirs(persist_dir, exist_ok=True)
        _client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        _collection = _client.get_or_create_collection(
            name=config.CHROMA_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        count = _collection.count()
        print(
            f"[VectorStore] ChromaDB collection '{config.CHROMA_COLLECTION}' ready "
            f"({count} docs) at {persist_dir}"
        )
    return _collection


# ── DB25 Hybrid Search Helper ─────────────────────────────────────────────────

def _db25_fuse(
    dense_results: dict,
    candidate_texts: list[str],
    query_text: str,
    top_k: int,
    rrf_k: int = 60,
) -> list[dict]:
    """Fuse Chroma dense results with BM25 scores via Reciprocal Rank Fusion.

    Args:
        dense_results: raw chromadb query result dict (ids, documents, metadatas, distances).
        candidate_texts: plain-text (decrypted) strings corresponding to each candidate.
        query_text: the raw user query string for BM25.
        top_k: number of results to return.
        rrf_k: RRF constant (default 60 per the original RRF paper).

    Returns:
        List of result dicts: {text, metadata, score}.
    """
    ids = dense_results["ids"][0]
    metadatas = dense_results["metadatas"][0]
    distances = dense_results["distances"][0]  # cosine distance (0=identical, 1=orthogonal)
    n = len(ids)

    if n == 0:
        return []

    # Dense rank: Chroma returns nearest first (lowest distance = rank 0)
    dense_rank = {doc_id: rank for rank, doc_id in enumerate(ids)}

    # BM25 rank over decrypted candidate texts
    tokenized = [t.lower().split() for t in candidate_texts]
    bm25 = BM25Okapi(tokenized)
    bm25_scores = bm25.get_scores(query_text.lower().split())
    # Rank descending by BM25 score (highest score = rank 0)
    bm25_order = sorted(range(n), key=lambda i: bm25_scores[i], reverse=True)
    bm25_rank = {bm25_order[rank]: rank for rank in range(n)}

    # RRF fusion
    fused = []
    for i, doc_id in enumerate(ids):
        rrf_score = 1.0 / (rrf_k + dense_rank[doc_id]) + 1.0 / (rrf_k + bm25_rank[i])
        # Convert cosine distance → similarity score (0–1)
        cosine_sim = max(0.0, 1.0 - distances[i])
        fused.append({
            "_idx": i,
            "_id": doc_id,
            "rrf_score": rrf_score,
            "score": cosine_sim,
            "metadata": metadatas[i],
            "text": candidate_texts[i],
        })

    fused.sort(key=lambda x: x["rrf_score"], reverse=True)

    return [
        {
            "text": r["text"],
            "metadata": {
                "source": r["metadata"].get("source"),
                "file_type": r["metadata"].get("file_type"),
                "tier": r["metadata"].get("tier"),
            },
            "score": r["score"],
        }
        for r in fused[:top_k]
    ]


# ── Public API ────────────────────────────────────────────────────────────────

def add_chunks(
    chunks: list[dict],
    embeddings: list[list[float]],
    doc_id: str,
    tier: str = "extended",
    session_token: str = "admin",
) -> int:
    """Store chunks with their embeddings and knowledge tier. Returns number of items added."""
    col = _get_collection()

    ids, docs, metadatas, vecs = [], [], [], []
    for i, (chunk, vector) in enumerate(zip(chunks, embeddings)):
        text = chunk.get("text", "")
        enc_text = encrypt_data(text)
        chunk_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}_{i}"))

        ids.append(chunk_id)
        docs.append(enc_text)           # stored document = encrypted text
        metadatas.append({
            "source":        chunk.get("source", "unknown"),
            "file_type":     chunk.get("file_type", "?"),
            "tier":          tier,
            "session_token": session_token,
        })
        vecs.append(vector)

    # ChromaDB batch upsert
    col.upsert(ids=ids, documents=docs, metadatas=metadatas, embeddings=vecs)
    return len(chunks)


def query(
    query_embedding: list[float],
    top_k: int | None = None,
    keyword: str | None = None,
    session_token: str = "admin",
) -> list[dict]:
    """Return top_k most similar chunks using DB25 hybrid search.

    DB25 = Dense (ChromaDB cosine ANN) + BM25, fused via RRF.
    Falls back to pure dense search when keyword is None.
    """
    k = top_k or config.TOP_K_VECTOR
    col = _get_collection()

    # RBAC where-clause: foundation docs are globally readable; session docs only by owner/admin
    if session_token == "admin":
        where_filter = None  # admin sees everything
    else:
        where_filter = {
            "$or": [
                {"tier": {"$eq": "foundation"}},
                {"session_token": {"$eq": session_token}},
            ]
        }

    # Oversample for BM25 re-ranking (4× oversample, min 20)
    fetch_k = max(k * 4, 20) if keyword else k

    query_kwargs: dict = dict(
        query_embeddings=[query_embedding],
        n_results=min(fetch_k, max(col.count(), 1)),
        include=["documents", "metadatas", "distances"],
    )
    if where_filter:
        query_kwargs["where"] = where_filter

    raw = col.query(**query_kwargs)

    # Decrypt texts for BM25 and output
    enc_texts = raw["documents"][0] if raw["documents"] else []
    plain_texts = [decrypt_data(enc) for enc in enc_texts]

    if keyword and plain_texts:
        # DB25: dense + BM25 fusion
        return _db25_fuse(raw, plain_texts, keyword, top_k=k)

    # Pure dense fallback (no keyword supplied)
    results = []
    ids = raw["ids"][0] if raw["ids"] else []
    metadatas = raw["metadatas"][0] if raw["metadatas"] else []
    distances = raw["distances"][0] if raw["distances"] else []
    for text, meta, dist in zip(plain_texts, metadatas, distances):
        results.append({
            "text": text,
            "metadata": {
                "source": meta.get("source"),
                "file_type": meta.get("file_type"),
                "tier": meta.get("tier"),
            },
            "score": max(0.0, 1.0 - dist),
        })
    return results[:k]


def list_documents(session_token: str = "admin") -> list[dict]:
    """Return unique source documents stored in the collection."""
    col = _get_collection()

    # Fetch all metadata (no embeddings needed)
    all_meta = col.get(include=["metadatas"])["metadatas"] or []

    seen, docs = set(), []
    for meta in all_meta:
        tier = meta.get("tier", "extended")
        tok  = meta.get("session_token", "")
        if session_token != "admin" and tier != "foundation" and tok != session_token:
            continue
        src = meta.get("source", "unknown")
        if src not in seen:
            seen.add(src)
            docs.append({
                "source":    src,
                "file_type": meta.get("file_type", "?"),
                "tier":      tier,
            })
    return docs


def get_all_text(session_token: str = "admin") -> str:
    """Return all document text in the knowledge base, concatenated."""
    col = _get_collection()

    all_data = col.get(include=["documents", "metadatas"])
    enc_docs  = all_data.get("documents") or []
    metadatas = all_data.get("metadatas") or []

    texts = []
    for enc_text, meta in zip(enc_docs, metadatas):
        tier = meta.get("tier", "extended")
        tok  = meta.get("session_token", "")
        if session_token != "admin" and tier != "foundation" and tok != session_token:
            continue
        text = decrypt_data(enc_text)
        if text:
            texts.append(text)
    return "\n\n".join(texts)


def delete_document(source_name: str, session_token: str = "admin") -> int:
    """Delete all chunks belonging to a source document. Returns deleted count."""
    col = _get_collection()

    if session_token == "admin":
        where_filter = {"source": {"$eq": source_name}}
    else:
        where_filter = {
            "$and": [
                {"source": {"$eq": source_name}},
                {"session_token": {"$eq": session_token}},
            ]
        }

    # Get IDs matching filter then delete
    result = col.get(where=where_filter, include=[])
    ids = result.get("ids") or []
    if ids:
        col.delete(ids=ids)
    return len(ids)


def delete_by_session(session_token: str) -> int:
    """Delete all chunks belonging to a specific session token."""
    if session_token in ("admin", "anonymous", ""):
        return 0
    col = _get_collection()
    result = col.get(
        where={"session_token": {"$eq": session_token}},
        include=[],
    )
    ids = result.get("ids") or []
    if ids:
        col.delete(ids=ids)
    return len(ids)


def count() -> int:
    """Return total chunk count in the collection."""
    return _get_collection().count()


def purge() -> None:
    """Wipe the entire ChromaDB collection and reset the in-memory client."""
    global _client, _collection
    if _client is not None and _collection is not None:
        try:
            _client.delete_collection(config.CHROMA_COLLECTION)
        except Exception:
            pass
    _client = None
    _collection = None
