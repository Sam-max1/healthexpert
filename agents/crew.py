"""CrewAI crew assembly — Ingestor Crew and Analyst Crew."""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["CREWAI_TRACING_ENABLED"] = "true"
os.environ["CREWAI_TELEMETRY_OPT_OUT"] = "true"
os.environ["OTEL_SDK_DISABLED"] = "true"

import config
from crewai import Agent, Task, Crew, Process
from agents.llm import get_llm
from agents.tools import (
    ingest_document,
    extract_and_store_entities,
    vector_search,
    graph_search,
    synthesize_answer,
)


def _make_llm():
    return get_llm()


# ── Agent definitions ─────────────────────────────────────────────────────────

def _ingestor_agent() -> Agent:
    return Agent(
        role="Document Ingestion Specialist",
        goal=(
            "Accurately load, chunk, embed, and store document data "
            "in both the vector database and the knowledge graph."
        ),
        backstory=(
            "You are an expert data engineer specializing in information systems. "
            "You process complex documents with precision, ensuring every fact "
            "is indexed and retrievable."
        ),
        tools=[ingest_document, extract_and_store_entities],
        llm=_make_llm(),
        verbose=True,
        allow_delegation=False,
    )


def _retriever_agent() -> Agent:
    return Agent(
        role="Hybrid Knowledge Retriever",
        goal=(
            "Retrieve the most relevant document passages using both semantic vector search "
            "and graph-based relationship traversal."
        ),
        backstory=(
            "You are a retrieval specialist with deep expertise in combining dense vector "
            "search with graph-augmented context to surface the most accurate information."
        ),
        tools=[vector_search, graph_search],
        llm=_make_llm(),
        verbose=True,
        allow_delegation=False,
    )


def _comprehensive_agent() -> Agent:
    from agents.llm import LocalLLM
    # Disabled KV cache for debugging
    kv_llm = LocalLLM(model=config.LLM_MODEL_ID, use_kv_cache=False)
    return Agent(
        role="Comprehensive Document Reader",
        goal="Read the entire knowledge base context and answer the user query accurately.",
        backstory="You are an expert analyst who extracts insights directly from the provided text.",
        llm=kv_llm,
        verbose=True,
        allow_delegation=False,
    )

def _gatekeeper_agent() -> Agent:
    return Agent(
        role="Context Verification Specialist",
        goal=(
            "Evaluate retrieved document text and determine if it contains "
            "any factual information relevant to answering a user's query."
        ),
        backstory=(
            "You are a strict verification specialist. Your job is to act as a firewall. "
            "You objectively read context and decide if it is sufficient to formulate an answer. "
            "You return ONLY 'YES' or 'NO'."
        ),
        llm=_make_llm(),
        verbose=True,
        allow_delegation=False,
    )


def _analyst_agent() -> Agent:
    return Agent(
        role="Information Analyst",
        goal=(
            "Synthesize retrieved context into clear, accurate, well-cited "
            "Markdown answers to user questions, adhering strictly to the provided context."
        ),
        backstory=(
            "You are a senior analyst with extensive experience "
            "interpreting complex documents. You communicate information clearly and precisely, "
            "and you never hallucinate or assume information beyond what is given."
        ),
        tools=[synthesize_answer],
        llm=_make_llm(),
        verbose=True,
        allow_delegation=False,
    )


# ── Crew runners ──────────────────────────────────────────────────────────────

def run_ingest_crew(file_path: str) -> str:
    """Run the ingestion crew for a single document. Returns result string."""
    agent = _ingestor_agent()

    task_ingest = Task(
        description=f"Ingest the document at: {file_path}",
        expected_output="Confirmation that the document was chunked, embedded, and stored.",
        agent=agent,
        tools=[ingest_document],
    )
    task_graph = Task(
        description=f"Extract key entities from the document at: {file_path} and store in graph DB.",
        expected_output="Confirmation that entities and relationships were stored in the graph database.",
        agent=agent,
        tools=[extract_and_store_entities],
    )
    crew = Crew(
        agents=[agent],
        tasks=[task_ingest, task_graph],
        process=Process.sequential,
        verbose=True,
    )
    result = crew.kickoff()
    return str(result)


def run_query_crew(query: str) -> str:
    """Run the retrieval + analysis crew for a user query. Returns Markdown answer."""
    comprehensive = _comprehensive_agent()
    gatekeeper    = _gatekeeper_agent()
    analyst       = _analyst_agent()

    # --- Phase 1: Comprehensive Inference ---
    from pipeline.vector_store import get_all_text
    kb_text = get_all_text()
    
    print(f"\n[Comprehensive Inference Phase] Triggering agent for: '{query}'")
    task_comprehensive = Task(
        description=f"Please answer the following query using ONLY the knowledge base provided below. If the answer is not in the knowledge base, say 'No information'.\n\nKNOWLEDGE BASE:\n{kb_text}\n\nQuery: {query}",
        expected_output="A detailed answer based ONLY on the documents.",
        agent=comprehensive
    )
    crew_comprehensive = Crew(
        agents=[comprehensive],
        tasks=[task_comprehensive],
        process=Process.sequential,
        verbose=True,
    )
    
    try:
        context_output = str(crew_comprehensive.kickoff())
    except Exception as e:
        print(f"KV Cache inference failed: {e}. Falling back to standard vector search.")
        from agents.tools import vector_search, graph_search
        try:
            vec_ctx = vector_search.func(query)
            graph_ctx = graph_search.func(query)
            context_output = f"--- Vector DB Results ---\n{vec_ctx}\n\n--- Graph DB Results ---\n{graph_ctx}"
        except Exception as fallback_e:
            context_output = f"Retrieval failed: {fallback_e}"


    # --- Phase 2: Gatekeeping ---
    task_gatekeeper = Task(
        description=(
            f"Review the following retrieved context to see if it contains enough information "
            f"to answer this user query: '{query}'.\n\n"
            f"CONTEXT:\n{context_output}\n\n"
            "If the context is empty, or states 'No relevant documents found', or does not contain "
            "information relevant to the query, answer with exactly and only 'NO'. "
            "Otherwise, if it contains useful facts, answer with exactly and only 'YES'."
        ),
        expected_output="Strictly 'YES' or 'NO'.",
        agent=gatekeeper,
    )
    crew_gatekeeper = Crew(
        agents=[gatekeeper],
        tasks=[task_gatekeeper],
        process=Process.sequential,
        verbose=True,
    )
    decision = str(crew_gatekeeper.kickoff()).strip().upper()

    if "YES" not in decision and "NO" in decision:
        return "Internal data does not have any information to answer the question."

    # --- Phase 3: Analysis ---
    task_analyze = Task(
        description=(
            f"Using the following retrieved context, synthesize a complete, "
            f"accurate, and well-cited Markdown answer to: '{query}'.\n\n"
            f"CONTEXT:\n{context_output}\n\n"
            "Use the synthesize_answer tool with JSON input {\"query\": ..., \"context\": ...}. "
            "IMPORTANT GUARDRAIL: You must answer using ONLY the retrieved context. "
            "Do not add external knowledge or hallucinate."
        ),
        expected_output=(
            "A structured Markdown response with bullet points, sections, "
            "and source citations [Source: filename]."
        ),
        agent=analyst,
    )
    crew_analyze = Crew(
        agents=[analyst],
        tasks=[task_analyze],
        process=Process.sequential,
        verbose=True,
    )
    result = crew_analyze.kickoff()
    return str(result)
