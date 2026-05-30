#!/usr/bin/env python3
"""Manage HealthExpert databases via docker compose.

Note: ChromaDB is embedded (in-process) and does not require Docker.
      This script manages only the Neo4j graph database container.
"""
import argparse
import subprocess
import sys
from pathlib import Path

COMPOSE_FILE = Path(__file__).parent / "docker-compose.yml"

def run_command(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {' '.join(cmd)}")
        sys.exit(e.returncode)
    except FileNotFoundError:
        print("Error: docker binary not found. Please ensure Docker is installed.")
        sys.exit(1)

def up():
    print("Starting Neo4j graph database...")
    run_command(["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"])
    print("Neo4j started successfully.")

def down():
    print("Stopping Neo4j graph database...")
    run_command(["docker", "compose", "-f", str(COMPOSE_FILE), "down"])
    print("Neo4j stopped successfully.")

def status():
    print("Checking Neo4j status...\n")
    run_command(["docker", "compose", "-f", str(COMPOSE_FILE), "ps"])

def main():
    parser = argparse.ArgumentParser(description="Manage HealthExpert graph database (Neo4j).")
    parser.add_argument("-up",   action="store_true", help="Spin up Neo4j")
    parser.add_argument("-down", action="store_true", help="Spin down Neo4j")

    args = parser.parse_args()

    if args.up and args.down:
        print("Error: Cannot specify both -up and -down.")
        sys.exit(1)

    if args.up:
        up()
    elif args.down:
        down()
    else:
        status()

if __name__ == "__main__":
    main()
