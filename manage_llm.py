#!/usr/bin/env python3
"""Manage HealthExpert LLM Microservices.

This script allows you to spin up, spin down, and check the status
of the local LLM inference endpoints (gen_llm.py and embed_llm.py).

Usage:
  python manage_llm.py status   # show status of LLM servers
  python manage_llm.py up       # start both LLM servers
  python manage_llm.py down     # stop both LLM servers
"""
import argparse
import subprocess
import time
import os
import sys
from pathlib import Path
import urllib.request

GEN_PORT = 8002
EMBED_PORT = 8003
BASE_DIR = Path(__file__).parent.resolve()

def _check_port(port: int) -> bool:
    """Check if a port is actively listening by making a simple HTTP request."""
    try:
        # Just a healthcheck to see if server responds, we expect 404 or 200
        req = urllib.request.Request(f"http://127.0.0.1:{port}/")
        urllib.request.urlopen(req, timeout=1)
        return True
    except urllib.error.URLError as e:
        # If it's an HTTPError (e.g. 404 Not Found), the server is alive
        if hasattr(e, 'code'):
            return True
        # ConnectionRefusedError usually means nothing is listening
        return False
    except Exception:
        return False

def _kill_port(port: int) -> None:
    """Kill any process listening on the given port."""
    try:
        # Using fuser to kill processes on the port
        subprocess.run(["fuser", "-k", f"{port}/tcp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        try:
            # Fallback to lsof if fuser is not available
            pids = subprocess.check_output(["lsof", "-t", f"-i:{port}"]).decode().strip().split('\n')
            for pid in pids:
                if pid:
                    subprocess.run(["kill", "-9", pid])
        except Exception:
            pass

def status() -> None:
    """Show the status of the LLM servers."""
    gen_alive = _check_port(GEN_PORT)
    embed_alive = _check_port(EMBED_PORT)

    print("\n══════════════════════════════════════════════════════════════")
    print("  HealthExpert — LLM Microservices Status")
    print("══════════════════════════════════════════════════════════════")
    
    print("\n── Generation LLM (agents/gen_llm.py) ───────────────────────")
    print(f"  Port      : {GEN_PORT}")
    print(f"  Status    : {'🟢 RUNNING' if gen_alive else '🔴 STOPPED'}")
    
    print("\n── Embedding LLM (agents/embed_llm.py) ──────────────────────")
    print(f"  Port      : {EMBED_PORT}")
    print(f"  Status    : {'🟢 RUNNING' if embed_alive else '🔴 STOPPED'}")
    print("─" * 62)
    print("")

def down() -> None:
    """Stop the LLM servers."""
    print("Stopping LLM services...")
    _kill_port(GEN_PORT)
    _kill_port(EMBED_PORT)
    
    # Also kill by script name as a fallback
    subprocess.run(["pkill", "-f", "agents/gen_llm.py"], stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-f", "agents/embed_llm.py"], stderr=subprocess.DEVNULL)
    
    time.sleep(1)
    print("LLM services stopped.")

def up(hf_mode: bool = False) -> None:
    """Start the LLM servers."""
    gen_alive = _check_port(GEN_PORT)
    embed_alive = _check_port(EMBED_PORT)

    if gen_alive and embed_alive:
        print("Both LLM services are already running.")
        return

    print("Starting LLM services...")
    
    env = os.environ.copy()
    if hf_mode:
        env["HF_MODE"] = "1"
        print("Running in HF CPU mode (HF_MODE=1)")

    # Start Embed LLM
    if not embed_alive:
        print(f"[1/2] Starting embed_llm on port {EMBED_PORT}...")
        subprocess.Popen(
            [sys.executable, str(BASE_DIR / "agents" / "embed_llm.py")],
            cwd=BASE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env
        )
    else:
        print(f"[1/2] embed_llm is already running on port {EMBED_PORT}.")

    # Start Gen LLM
    if not gen_alive:
        print(f"[2/2] Starting gen_llm on port {GEN_PORT}...")
        subprocess.Popen(
            [sys.executable, str(BASE_DIR / "agents" / "gen_llm.py")],
            cwd=BASE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env
        )
    else:
        print(f"[2/2] gen_llm is already running on port {GEN_PORT}.")

    print("\nWaiting for services to initialize...")
    time.sleep(3)
    status()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage HealthExpert LLM microservices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    
    # Optional positional argument for the command
    parser.add_argument("command", nargs="?", choices=["up", "down", "status"], default="status",
                        help="Action to perform (default: status)")
    parser.add_argument("-hf", "--hf", action="store_true", help="Start servers in HuggingFace/CPU mode")
    
    args = parser.parse_args()

    if args.command == "up":
        up(hf_mode=args.hf)
    elif args.command == "down":
        down()
    else:
        status()


if __name__ == "__main__":
    main()
