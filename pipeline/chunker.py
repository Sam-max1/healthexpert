"""Text chunker using LangChain RecursiveCharacterTextSplitter.

LangChain 1.x moved the splitter to langchain_text_splitters; fall back
to langchain.text_splitter for older installs.
"""
from __future__ import annotations
from typing import Any
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config


def chunk_documents(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split a list of loaded document pages into smaller overlapping chunks.

    Returns a flat list of chunk dicts, each with keys:
        text, source, page, chunk_index
    (flat structure so tools.py can access chunk['source'] directly)
    """
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except ImportError:
        from langchain.text_splitter import RecursiveCharacterTextSplitter  # type: ignore

    splitter = RecursiveCharacterTextSplitter(
        chunk_size    = config.CHUNK_SIZE,
        chunk_overlap = config.CHUNK_OVERLAP,
        separators    = ["\n\n", "\n", ". ", " ", ""],
    )
    chunks = []
    for doc in docs:
        texts = splitter.split_text(doc["text"])
        meta = doc.get("metadata", {})
        source = doc.get("source", meta.get("source", "unknown"))
        page   = doc.get("page",   meta.get("page", 0))
        for i, text in enumerate(texts):
            chunk_meta = {**meta, "source": source, "page": page, "chunk_index": i}
            chunks.append({
                "text": text,
                **chunk_meta
            })
    return chunks
