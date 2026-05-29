"""Weaviate vector store wrapper."""
from __future__ import annotations
import uuid, sys, os
import weaviate
import weaviate.classes.config as wv_config
from weaviate.classes.query import MetadataQuery

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
from pipeline.security import encrypt_data, decrypt_data

_client = None

def _get_client():
    global _client
    if _client is None:
        _client = weaviate.connect_to_local(port=8080, grpc_port=50051)
        if not _client.collections.exists(config.WEAVIATE_CLASS):
            _client.collections.create(
                name=config.WEAVIATE_CLASS,
                properties=[
                    wv_config.Property(name="encrypted_text", data_type=wv_config.DataType.TEXT),
                    wv_config.Property(name="source", data_type=wv_config.DataType.TEXT),
                    wv_config.Property(name="file_type", data_type=wv_config.DataType.TEXT),
                    wv_config.Property(name="tier", data_type=wv_config.DataType.TEXT),
                ],
            )
        col = _client.collections.get(config.WEAVIATE_CLASS)
        count = len(list(col.iterator()))
        print(f"[VectorStore] Collection '{config.WEAVIATE_CLASS}' ready "
              f"({count} docs).")
    return _client

def add_chunks(chunks: list[dict], embeddings: list[list[float]], doc_id: str, tier: str = "extended") -> int:
    """Store chunks with their embeddings and knowledge tier. Returns number of items added."""
    client = _get_client()
    col = client.collections.get(config.WEAVIATE_CLASS)
    
    with col.batch.dynamic() as batch:
        for i, (chunk, vector) in enumerate(zip(chunks, embeddings)):
            text = chunk.get("text", "")
            enc_text = encrypt_data(text)
            properties = {
                "encrypted_text": enc_text,
                "source": chunk.get("source", "unknown"),
                "file_type": chunk.get("file_type", "?"),
                "tier": tier
            }
            batch.add_object(
                properties=properties,
                vector=vector,
                uuid=uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}_{i}")
            )
    return len(chunks)

def query(query_embedding: list[float], top_k: int = None, keyword: str = None) -> list[dict]:
    """Return top_k most similar chunks as list of {text, metadata, score}."""
    k = top_k or config.TOP_K_VECTOR
    client = _get_client()
    col = client.collections.get(config.WEAVIATE_CLASS)
    
    # Weaviate semantic keyword search (hybrid search)
    if keyword:
        res = col.query.hybrid(
            query=keyword,
            vector=query_embedding,
            limit=k,
            return_metadata=MetadataQuery(distance=True, score=True)
        )
    else:
        res = col.query.near_vector(
            near_vector=query_embedding,
            limit=k,
            return_metadata=MetadataQuery(distance=True)
        )
        
    results = []
    for obj in res.objects:
        props = obj.properties
        # Decrypt text
        text = decrypt_data(props.get("encrypted_text", ""))
        
        metadata = {
            "source": props.get("source"),
            "file_type": props.get("file_type"),
            "tier": props.get("tier")
        }
        score = 1.0 - (obj.metadata.distance if obj.metadata.distance is not None else 0.0)
        
        results.append({
            "text": text,
            "metadata": metadata,
            "score": score
        })
    return results

def list_documents() -> list[dict]:
    """Return unique source documents stored in the collection."""
    client = _get_client()
    col = client.collections.get(config.WEAVIATE_CLASS)
    seen, docs = set(), []
    for obj in col.iterator():
        props = obj.properties
        src = props.get("source", "unknown")
        if src not in seen:
            seen.add(src)
            docs.append({
                "source": src, 
                "file_type": props.get("file_type", "?"),
                "tier": props.get("tier", "extended")
            })
    return docs

def get_all_text() -> str:
    """Return all document text in the knowledge base, concatenated."""
    client = _get_client()
    col = client.collections.get(config.WEAVIATE_CLASS)
    texts = []
    for obj in col.iterator():
        enc_text = obj.properties.get("encrypted_text", "")
        text = decrypt_data(enc_text)
        if text:
            texts.append(text)
    return "\n\n".join(texts)

def delete_document(source_name: str) -> int:
    """Delete all chunks belonging to a source document. Returns deleted count."""
    client = _get_client()
    col = client.collections.get(config.WEAVIATE_CLASS)
    res = col.query.fetch_objects(
        filters=wv_config.Filter.by_property("source").equal(source_name)
    )
    deleted = 0
    for obj in res.objects:
        col.data.delete_by_id(obj.uuid)
        deleted += 1
    return deleted

def count() -> int:
    client = _get_client()
    col = client.collections.get(config.WEAVIATE_CLASS)
    return len(list(col.iterator()))
