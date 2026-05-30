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
import shutil
from pathlib import Path

COMPOSE_FILE = Path(__file__).parent / "docker-compose.yml"
CHROMA_DIR   = Path(__file__).parent / "data" / "chroma_db"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Command failed: {' '.join(cmd)}")
        sys.exit(e.returncode)
    except FileNotFoundError:
        print("[ERROR] docker binary not found — please install Docker.")
        sys.exit(1)


def _chroma_info() -> dict:
    """Return ChromaDB directory stats."""
    exists = CHROMA_DIR.exists()
    size_mb = 0.0
    file_count = 0
    if exists:
        for f in CHROMA_DIR.rglob("*"):
            if f.is_file():
                size_mb   += f.stat().st_size / 1024 ** 2
                file_count += 1
    return {"exists": exists, "path": str(CHROMA_DIR), "size_mb": size_mb, "files": file_count}


# ── Actions ───────────────────────────────────────────────────────────────────

def up() -> None:
    """Start Neo4j docker container."""
    print("► Starting Neo4j (Graph DB)...")
    _run(["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"])
    print("  Neo4j started. Browser UI → http://localhost:7474")
    print("  Bolt endpoint → bolt://localhost:7687")


def down() -> None:
    """Stop Neo4j docker container."""
    print("► Stopping Neo4j (Graph DB)...")
    _run(["docker", "compose", "-f", str(COMPOSE_FILE), "down"])
    print("  Neo4j stopped.")


def chroma_status() -> None:
    """Print ChromaDB data directory info."""
    info = _chroma_info()
    print("\n── ChromaDB (Vector Store) ──────────────────────────────────")
    print(f"  Type      : Embedded (in-process, no server)")
    print(f"  Data dir  : {info['path']}")
    if info["exists"]:
        print(f"  Status    : PRESENT  ({info['files']} files, {info['size_mb']:.2f} MB)")
    else:
        print(f"  Status    : EMPTY (will be created on first ingest)")
    print("  Purge via : POST /api/admin/purge  (UI admin panel)")
    print("─" * 60)


def status() -> None:
    """Show status of all databases."""
    print("\n══════════════════════════════════════════════════════════════")
    print("  HealthExpert — Database Status")
    print("══════════════════════════════════════════════════════════════")

    # ChromaDB
    chroma_status()

    # Neo4j via docker compose
    print("\n── Neo4j (Graph DB) ─────────────────────────────────────────")
    print(f"  Type      : Docker container (docker-compose.yml)")
    print(f"  Browser   : http://localhost:7474")
    print(f"  Bolt      : bolt://localhost:7687")
    print(f"  Container status:")
    _run(["docker", "compose", "-f", str(COMPOSE_FILE), "ps"])
    print("─" * 60 + "\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage HealthExpert databases (Neo4j docker + ChromaDB embedded).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python manage_db.py              # show all db status\n"
            "  python manage_db.py -up          # start Neo4j\n"
            "  python manage_db.py -down        # stop  Neo4j\n"
            "  python manage_db.py -chroma      # ChromaDB info only\n"
        ),
    )
    parser.add_argument("-up",     action="store_true", help="Start Neo4j docker container")
    parser.add_argument("-down",   action="store_true", help="Stop  Neo4j docker container")
    parser.add_argument("-chroma", action="store_true", help="Show ChromaDB data directory info")

    args = parser.parse_args()

    if args.up and args.down:
        print("[ERROR] Cannot specify both -up and -down.")
        sys.exit(1)

    if args.up:
        up()
    elif args.down:
        down()
    elif args.chroma:
        chroma_status()
    else:
        status()


if __name__ == "__main__":
    main()
