#!/usr/bin/env python3
"""
Document AI Expert — Standalone CLI
=========================================
Usage:
  python healthexpert.py ingest <file_path>
  python healthexpert.py query "<question>"
  python healthexpert.py list
  python healthexpert.py clear <source_name>
  python healthexpert.py status
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
import config
from pipeline import vector_store, graph_store, embedder, document_loader, chunker
from agents.crew import run_ingest_crew, run_query_crew


def cmd_ingest(file_path: str) -> None:
    if not os.path.exists(file_path):
        print(f"[ERROR] File not found: {file_path}")
        sys.exit(1)
    print(f"[INGEST] Processing: {file_path}")
    result = run_ingest_crew(file_path)
    print(f"\n[RESULT]\n{result}")


def cmd_query(question: str) -> None:
    if vector_store.count() == 0:
        print("[ERROR] No documents ingested. Run: python healthexpert.py ingest <file>")
        sys.exit(1)
    print(f"[QUERY] {question}\n")
    answer = run_query_crew(question)
    print("\n" + "=" * 60)
    print(answer)
    print("=" * 60)


def cmd_list() -> None:
    docs = vector_store.list_documents()
    if not docs:
        print("[INFO] No documents ingested yet.")
        return
    print(f"\n{'SOURCE':<40} {'TYPE':<10}")
    print("-" * 52)
    for d in docs:
        print(f"{d['source']:<40} {d['file_type']:<10}")
    print(f"\nTotal: {len(docs)} document(s), {vector_store.count()} chunk(s)")


def cmd_clear(source_name: str) -> None:
    n = vector_store.delete_document(source_name)
    graph_store.delete_source(source_name)
    print(f"[CLEAR] Deleted {n} chunks for '{source_name}'")


def cmd_status() -> None:
    print("\n── Document AI Expert Status ──")
    print(f"  LLM Endpoint : {config.LLM_BASE_URL}")
    print(f"  LLM Model    : {config.LLM_MODEL_ID}")
    print(f"  Embedding    : {config.EMBEDDING_MODEL} ({config.EMBEDDING_DEVICE})")
    print(f"  Vector DB    : {vector_store.count()} chunks  [{config.WEAVIATE_URL}]")
    g = graph_store.get_stats()
    if g.get("available"):
        print(f"  Graph DB     : {g['nodes']} nodes, {g['relationships']} relationships")
    else:
        print("  Graph DB     : OFFLINE (vector-only mode)")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Document AI Expert — CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    p_ingest = sub.add_parser("ingest", help="Ingest a document")
    p_ingest.add_argument("file", help="Path to document file")

    p_query = sub.add_parser("query", help="Ask a question")
    p_query.add_argument("question", help="Question string")

    sub.add_parser("list",   help="List ingested documents")
    sub.add_parser("status", help="Show system status")

    p_clear = sub.add_parser("clear", help="Remove a document")
    p_clear.add_argument("source", help="Source filename to remove")

    args = parser.parse_args()

    if args.command == "ingest":
        cmd_ingest(args.file)
    elif args.command == "query":
        cmd_query(args.question)
    elif args.command == "list":
        cmd_list()
    elif args.command == "clear":
        cmd_clear(args.source)
    elif args.command == "status":
        cmd_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
