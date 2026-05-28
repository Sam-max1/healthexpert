"""ChromaDB vector store wrapper."""
from __future__ import annotations
import uuid, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config

_client     = None
_collection = None


def _get_collection():
    global _client, _collection
    if _collection is None:
        import chromadb
        os.makedirs(config.CHROMA_PERSIST_DIR, exist_ok=True)
        _client     = chromadb.PersistentClient(path=config.CHROMA_PERSIST_DIR)
        _collection = _client.get_or_create_collection(
            name=config.CHROMA_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
        print(f"[VectorStore] Collection '{config.CHROMA_COLLECTION}' ready "
              f"({_collection.count()} docs).")
    return _collection


def add_chunks(chunks: list[dict], embeddings: list[list[float]], doc_id: str, tier: str = "extended") -> int:
    """Store chunks with their embeddings and knowledge tier. Returns number of items added."""
    col = _get_collection()
    ids        = [f"{doc_id}_{i}" for i in range(len(chunks))]
    documents  = [c["text"] for c in chunks]
    metadatas  = [{k: v for k, v in c.items() if k != "text"} for c in chunks]
    for m in metadatas:
        m["tier"] = tier
    col.add(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)
    return len(chunks)


def query(query_embedding: list[float], top_k: int = None) -> list[dict]:
    """Return top_k most similar chunks as list of {text, metadata, score}."""
    k   = top_k or config.TOP_K_VECTOR
    col = _get_collection()
    if col.count() == 0:
        return []
    res = col.query(query_embeddings=[query_embedding], n_results=min(k, col.count()))
    results = []
    for i in range(len(res["ids"][0])):
        results.append({
            "text":     res["documents"][0][i],
            "metadata": res["metadatas"][0][i],
            "score":    1 - res["distances"][0][i],  # cosine similarity
        })
    return results


def list_documents() -> list[dict]:
    """Return unique source documents stored in the collection."""
    col = _get_collection()
    if col.count() == 0:
        return []
    all_meta = col.get(include=["metadatas"])["metadatas"]
    seen, docs = set(), []
    for m in all_meta:
        src = m.get("source", "unknown")
        if src not in seen:
            seen.add(src)
            docs.append({
                "source": src, 
                "file_type": m.get("file_type", "?"),
                "tier": m.get("tier", "extended")
            })
    return docs

def get_all_text() -> str:
    """Return all document text in the knowledge base, concatenated."""
    col = _get_collection()
    if col.count() == 0:
        return ""
    res = col.get(include=["documents"])
    return "\n\n".join(res.get("documents", []))


def delete_document(source_name: str) -> int:
    """Delete all chunks belonging to a source document. Returns deleted count."""
    col    = _get_collection()
    result = col.get(where={"source": source_name}, include=["metadatas"])
    ids    = result.get("ids", [])
    if ids:
        col.delete(ids=ids)
    return len(ids)


def count() -> int:
    return _get_collection().count()
