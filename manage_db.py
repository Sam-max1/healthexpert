#!/usr/bin/env python3
"""Manage HealthExpert databases.

Database plan:
  ┌─────────────────┬────────────────────────────────────────────────────┐
  │ Database        │ Management                                          │
  ├─────────────────┼────────────────────────────────────────────────────┤
  │ ChromaDB        │ Embedded (in-process). No Docker container.        │
  │  (Vector Store) │ Data stored at: data/chroma_db/                    │
  │                 │ Managed by: pipeline/vector_store.py               │
  │                 │ Use /api/admin/purge (UI) or wipe data/chroma_db/  │
  ├─────────────────┼────────────────────────────────────────────────────┤
  │ Kuzu            │ Embedded (in-process). No Docker container.        │
  │  (Graph DB)     │ Data stored at: data/kuzu_db/                      │
  │                 │ Managed by: pipeline/graph_store.py                │
  │                 │ Use /api/admin/purge (UI) or wipe data/kuzu_db/    │
  └─────────────────┴────────────────────────────────────────────────────┘

Usage:
  python manage_db.py          # show status of all databases
  python manage_db.py -chroma  # show ChromaDB data directory info
  python manage_db.py -kuzu    # show Kuzu data directory info
"""
import argparse
import sys
import os
from pathlib import Path

CHROMA_DIR   = Path(__file__).parent / "data" / "chroma_db"
KUZU_DIR     = Path(__file__).parent / "data" / "kuzu_db"


def _dir_info(path: Path) -> dict:
    exists = path.exists()
    size_mb = 0.0
    file_count = 0
    if exists:
        for f in path.rglob("*"):
            if f.is_file():
                size_mb    += f.stat().st_size / 1024 ** 2
                file_count += 1
    return {"exists": exists, "path": str(path), "size_mb": size_mb, "files": file_count}


def chroma_status() -> None:
    info = _dir_info(CHROMA_DIR)
    print("\n── ChromaDB (Vector Store) ──────────────────────────────────")
    print(f"  Type      : Embedded (in-process, no server)")
    print(f"  Data dir  : {info['path']}")
    if info["exists"]:
        print(f"  Status    : PRESENT  ({info['files']} files, {info['size_mb']:.2f} MB)")
    else:
        print(f"  Status    : EMPTY (will be created on first ingest)")
    print("─" * 60)

def kuzu_status() -> None:
    info = _dir_info(KUZU_DIR)
    print("\n── Kuzu (Graph DB) ──────────────────────────────────────────")
    print(f"  Type      : Embedded (in-process, no server)")
    print(f"  Data dir  : {info['path']}")
    if info["exists"]:
        print(f"  Status    : PRESENT  ({info['files']} files, {info['size_mb']:.2f} MB)")
    else:
        print(f"  Status    : EMPTY (will be created on first ingest)")
    print("─" * 60)


def status() -> None:
    print("\n══════════════════════════════════════════════════════════════")
    print("  HealthExpert — Database Status")
    print("══════════════════════════════════════════════════════════════")
    chroma_status()
    kuzu_status()
    print("")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage HealthExpert databases",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-chroma", action="store_true", help="Show ChromaDB info")
    parser.add_argument("-kuzu",   action="store_true", help="Show Kuzu DB info")
    args = parser.parse_args()

    if args.chroma: chroma_status()
    elif args.kuzu: kuzu_status()
    else: status()


if __name__ == "__main__":
    main()