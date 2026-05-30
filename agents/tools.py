"""CrewAI tools — document ingestion, vector search, graph search, LLM synthesis."""
from __future__ import annotations
import json, uuid, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from crewai.tools import tool
import config
from pipeline import document_loader, chunker, embedder, vector_store, graph_store
from agents.llm import get_llm


# ── Ingestion Tools ────────────────────────────────────────────────────────────

@tool("IngestDocumentTool")
def ingest_document(file_path: str) -> str:
    """Load, chunk, embed, and store a document in the vector database.
    Input: absolute path to the document file.
    Returns: ingestion summary string.
    """
    try:
        doc_id   = uuid.uuid4().hex[:8]
        docs     = document_loader.load_document(file_path)
        chunks   = chunker.chunk_documents(docs)
        if not chunks:
            return f"No text extracted from {file_path}"
        texts      = [c["text"] for c in chunks]
        embeddings = embedder.embed_texts(texts)
        session_token = config.current_session.get()
        added      = vector_store.add_chunks(chunks, embeddings, doc_id, tier="extended", session_token=session_token)
        return (f"Ingested '{os.path.basename(file_path)}': "
                f"{len(docs)} pages → {added} chunks stored (id={doc_id})")
    except Exception as e:
        return f"Ingestion failed: {e}"


@tool("ExtractAndStoreEntitiesTool")
def extract_and_store_entities(file_path: str) -> str:
    """Extract key entities from a document and store in the graph database.
    Input: absolute path to the document file.
    Returns: entity extraction summary.
    """
    if not graph_store.is_available():
        return "Graph DB unavailable — skipped entity extraction."
    try:
        docs   = document_loader.load_document(file_path)
        source = os.path.basename(file_path)
        # Sample first 3 pages for entity extraction (avoid huge prompts)
        sample_text = "\n\n".join(d["text"] for d in docs[:3])[:3000]
        llm    = get_llm()
        prompt = (
            "Extract key entities from the text below.\n"
            "Return a JSON array of objects with keys: name, type, relations.\n"
            "type must be a broad category like: Person, Organization, Location, Concept, Event, Document, Object, Rule.\n"
            "relations is a list of {target, rel} objects.\n"
            "Return ONLY the JSON array, no explanation.\n\n"
            f"TEXT:\n{sample_text}\n\nJSON:"
        )
        raw = llm.call([{"role": "user", "content": prompt}])
        # Find JSON array in the response
        start = raw.find("[")
        end   = raw.rfind("]") + 1
        if start == -1 or end == 0:
            return "No entities extracted (LLM returned no JSON)."
        entities = json.loads(raw[start:end])
        session_token = config.current_session.get()
        graph_store.store_entities(entities, source, tier="extended", session_token=session_token)
        return f"Stored {len(entities)} entities from '{source}' in graph DB."
    except Exception as e:
        return f"Entity extraction failed: {e}"


# ── Retrieval Tools ────────────────────────────────────────────────────────────

@tool("VectorSearchTool")
def vector_search(query: str) -> str:
    """Search the vector database for relevant text chunks.
    Input: query string.
    Returns: formatted context passages with source citations.
    """
    try:
        q_emb   = embedder.embed_query(query)
        session_token = config.current_session.get()
        results = vector_store.query(q_emb, top_k=config.TOP_K_VECTOR, keyword=query, session_token=session_token)
        if not results:
            return "No relevant documents found in vector store."
        passages = []
        for i, r in enumerate(results, 1):
            src   = r["metadata"].get("source", "unknown")
            score = r["score"]
            passages.append(f"[{i}] (source: {src}, relevance: {score:.2f})\n{r['text']}")
        return "\n\n---\n\n".join(passages)
    except Exception as e:
        return f"Vector search failed: {e}"


@tool("GraphSearchTool")
def graph_search(entities: str) -> str:
    """Search the graph database for related entities.
    Input: comma-separated entity names.
    Returns: related entity context or unavailable message.
    """
    if not graph_store.is_available():
        return "Graph DB unavailable."
    try:
        names   = [e.strip() for e in entities.split(",") if e.strip()]
        session_token = config.current_session.get()
        related = graph_store.query_related(names, hops=2, session_token=session_token)
        if not related:
            return "No graph relationships found."
        return "Related entities from knowledge graph:\n" + "\n".join(f"- {r}" for r in related)
    except Exception as e:
        return f"Graph search failed: {e}"


# ── Synthesis Tool ─────────────────────────────────────────────────────────────

@tool("SynthesizeAnswerTool")
def synthesize_answer(context_and_query: str) -> str:
    """Synthesize a final answer from retrieved context.
    Input: JSON string with keys 'query' and 'context'.
    Returns: Markdown-formatted answer with citations.
    """
    try:
        data    = json.loads(context_and_query)
        query   = data.get("query", "")
        context = data.get("context", "")
    except Exception:
        query, context = context_and_query, ""

    llm    = get_llm()
    prompt = (
        "You are an expert Information Analyst.\n"
        "Your task is to answer the question using ONLY the provided context.\n"
        "CRITICAL INSTRUCTIONS:\n"
        "1. STRICT GROUNDING: You must not use any external knowledge. If the information is not present in the context, do not hallucinate or make assumptions.\n"
        "2. ZERO RETRIEVAL GUARDRAIL: If the provided context is empty, irrelevant, or does not contain the answer, you must output EXACTLY and ONLY this sentence:\n"
        "'Internal data does not have any information to answer the question.'\n"
        "3. FORMAT: If you can answer the question based on the context, format your response in Markdown with a clear structure, bullet points for key facts, source citations like [Source: filename], and a 'Summary' section at the end.\n\n"
        f"CONTEXT:\n{context}\n\n"
        f"QUESTION: {query}\n\n"
        "ANSWER:"
    )
    return llm.call([{"role": "user", "content": prompt}])
