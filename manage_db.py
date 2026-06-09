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
  │ Neo4j           │ Docker container via docker-compose.yml            │
  │  (Graph DB)     │ Ports: 7474 (browser), 7687 (bolt)                 │
  │                 │ DISABLED in HF mode (Docker-in-Docker unavailable) │
  │                 │ Managed by: this script  (-up / -down / -status)   │
  └─────────────────┴────────────────────────────────────────────────────┘

Usage:
  python manage_db.py          # show status of all databases
  python manage_db.py -up      # start Neo4j container
  python manage_db.py -down    # stop  Neo4j container
  python manage_db.py -chroma  # show ChromaDB data directory info
"""
import argparse
import subprocess
import sys
import os
from pathlib import Path

COMPOSE_FILE = Path(__file__).parent / "docker-compose.yml"
CHROMA_DIR   = Path(__file__).parent / "data" / "chroma_db"

# Detect HF mode from env var (set by start.sh) or from HF Spaces detection
IS_HF_SPACE  = bool(os.getenv("SPACE_ID")) or os.getenv("HF_MODE") == "1"


def _run(cmd: list[str]) -> None:
    if IS_HF_SPACE and "docker" in cmd:
        print("[WARNING] Docker-in-Docker is not supported on HuggingFace Spaces. Neo4j command bypassed.")
        return
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Command failed: {' '.join(cmd)}")
        sys.exit(e.returncode)
    except FileNotFoundError:
        print("[ERROR] docker binary not found — please install Docker.")
        sys.exit(1)


def _chroma_info() -> dict:
    exists = CHROMA_DIR.exists()
    size_mb = 0.0
    file_count = 0
    if exists:
        for f in CHROMA_DIR.rglob("*"):
            if f.is_file():
                size_mb    += f.stat().st_size / 1024 ** 2
                file_count += 1
    return {"exists": exists, "path": str(CHROMA_DIR), "size_mb": size_mb, "files": file_count}


def up() -> None:
    if IS_HF_SPACE:
        print("[INFO] Neo4j is disabled in HF mode. Skipping start.")
        return
    print("► Starting Neo4j (Graph DB)...")
    _run(["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"])
    print("  Neo4j started. Browser UI → http://localhost:7474")


def down() -> None:
    if IS_HF_SPACE:
        print("[INFO] Neo4j is disabled in HF mode. Skipping stop.")
        return
    print("► Stopping Neo4j (Graph DB)...")
    _run(["docker", "compose", "-f", str(COMPOSE_FILE), "down"])


def chroma_status() -> None:
    info = _chroma_info()
    print("\n── ChromaDB (Vector Store) ──────────────────────────────────")
    print(f"  Type      : Embedded (in-process, no server)")
    print(f"  Data dir  : {info['path']}")
    if info["exists"]:
        print(f"  Status    : PRESENT  ({info['files']} files, {info['size_mb']:.2f} MB)")
    else:
        print(f"  Status    : EMPTY (will be created on first ingest)")
    print("─" * 60)


def status() -> None:
    hf_label = "  [HuggingFace / CPU Mode — Neo4j disabled]" if IS_HF_SPACE else ""
    print("\n══════════════════════════════════════════════════════════════")
    print("  HealthExpert — Database Status" + hf_label)
    print("══════════════════════════════════════════════════════════════")
    chroma_status()
    print("\n── Neo4j (Graph DB) ─────────────────────────────────────────")
    if IS_HF_SPACE:
        print("  Status    : DISABLED (HuggingFace / CPU mode constraint)")
        print("  Reason    : Docker-in-Docker is not available on HF Spaces")
    else:
        print(f"  Type      : Docker container (docker-compose.yml)")
        _run(["docker", "compose", "-f", str(COMPOSE_FILE), "ps"])
    print("─" * 60 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage HealthExpert databases",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-up",     action="store_true", help="Start Neo4j container")
    parser.add_argument("-down",   action="store_true", help="Stop Neo4j container")
    parser.add_argument("-chroma", action="store_true", help="Show ChromaDB info")
    args = parser.parse_args()

    if args.up:     up()
    elif args.down: down()
    elif args.chroma: chroma_status()
    else: status()


if __name__ == "__main__":
    main()