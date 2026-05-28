"""Embedding client — calls the embed_llm.py HTTP server on port 8003.

Instead of loading a model in-process, this module sends requests to the
standalone embed_llm.py Flask service (BAAI/bge-m3 via FlagEmbedding).

Endpoints used:
    POST http://127.0.0.1:8003/v1/embeddings        → dense vectors
    POST http://127.0.0.1:8003/v1/embeddings/multi  → dense + sparse + ColBERT
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config
import requests
import logging

log = logging.getLogger("embedder")


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Return a list of dense embedding vectors for the given texts.

    Delegates to the embed_llm.py server (POST /v1/embeddings).
    Response shape follows the OpenAI embeddings API convention.
    """
    try:
        resp = requests.post(
            config.EMBED_EMBEDDINGS_URL,
            json    = {"input": texts},
            timeout = config.EMBEDDING_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        # data["data"] is a list of {"index": i, "embedding": [...]}
        # Sort by index to preserve input order
        items = sorted(data["data"], key=lambda d: d["index"])
        return [item["embedding"] for item in items]
    except requests.exceptions.ConnectionError:
        log.error("[Embedder] Cannot connect to embed_llm server at %s — is it running?",
                  config.EMBED_BASE_URL)
        raise
    except Exception as exc:
        log.error("[Embedder] Request failed: %s", exc)
        raise


def embed_query(query: str) -> list[float]:
    """Embed a single query string. Returns one dense vector."""
    return embed_texts([query])[0]


def embed_texts_multi(
    sentences_1: list[str],
    sentences_2: list[str] | None = None,
    weights: list[float] | None   = None,
) -> dict:
    """Return dense + sparse (lexical) + ColBERT embeddings and scores.

    Delegates to POST /v1/embeddings/multi on the embed_llm server.
    Useful when you need full hybrid retrieval scores beyond dense vectors.

    Returns the raw response dict from the server.
    """
    payload: dict = {"sentences_1": sentences_1}
    if sentences_2 is not None:
        payload["sentences_2"] = sentences_2
    if weights is not None:
        payload["weights"] = weights
    try:
        resp = requests.post(
            f"{config.EMBED_BASE_URL}/v1/embeddings/multi",
            json    = payload,
            timeout = config.EMBEDDING_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        log.error("[Embedder] Cannot connect to embed_llm server at %s — is it running?",
                  config.EMBED_BASE_URL)
        raise
    except Exception as exc:
        log.error("[Embedder] Multi-embed request failed: %s", exc)
        raise
