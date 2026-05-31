#!/bin/bash
# scripts/test_hf_docker.sh
#
# Local test harness for the HuggingFace Docker image.
# Simulates HF Spaces hardware constraints (2 vCPU, 12 GB RAM, 16 GB disk)
# so you can validate the build locally before pushing to HuggingFace.
#
# Usage:
#   bash scripts/test_hf_docker.sh              # Build + run (admin mode)
#   bash scripts/test_hf_docker.sh --noadmin    # Build + run (public mode)
#   bash scripts/test_hf_docker.sh --build-only # Build image only, don't run
#   bash scripts/test_hf_docker.sh --no-build   # Skip build, just run existing image
#   bash scripts/test_hf_docker.sh --stop       # Stop running container
#
# Requirements:
#   - Docker installed and running
#   - At least 12 GB RAM available on host
#   - At least 16 GB free disk space

set -e

IMAGE_NAME="healthexpert-hf"
CONTAINER_NAME="healthexpert-hf-test"
PORT=7860

# ── Parse arguments ────────────────────────────────────────────────────────────
DO_BUILD=1
DO_RUN=1
NOADMIN=0

for arg in "$@"; do
  case "$arg" in
    --build-only) DO_RUN=0 ;;
    --no-build)   DO_BUILD=0 ;;
    --noadmin)    NOADMIN=1 ;;
    --stop)
      echo "Stopping container: $CONTAINER_NAME"
      docker stop "$CONTAINER_NAME" 2>/dev/null && docker rm "$CONTAINER_NAME" 2>/dev/null || true
      echo "Done."
      exit 0
      ;;
  esac
done

# ── Stop any existing test container ──────────────────────────────────────────
if docker ps -q --filter "name=$CONTAINER_NAME" | grep -q .; then
  echo "Stopping existing container: $CONTAINER_NAME ..."
  docker stop "$CONTAINER_NAME" > /dev/null
  docker rm   "$CONTAINER_NAME" > /dev/null
fi

# ── Build ─────────────────────────────────────────────────────────────────────
if [ "$DO_BUILD" -eq 1 ]; then
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  Building HuggingFace Docker image: $IMAGE_NAME"
  echo "  Using: Dockerfile.hf"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  
  # Run from repo root
  SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
  REPO_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
  
  docker build \
    -f "$REPO_ROOT/Dockerfile.hf" \
    -t "$IMAGE_NAME" \
    "$REPO_ROOT"
  
  echo ""
  echo "✅ Build complete: $IMAGE_NAME"
  echo "   Image size: $(docker image inspect $IMAGE_NAME --format='{{.Size}}' | awk '{printf "%.1f GB", $1/1024/1024/1024}')"
fi

# ── Run ───────────────────────────────────────────────────────────────────────
if [ "$DO_RUN" -eq 1 ]; then
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  Starting container: $CONTAINER_NAME"
  echo "  Simulating HF Spaces constraints:"
  echo "    CPU: 2 vCPU (--cpus=2)"
  echo "    RAM: 12 GB  (--memory=12g)"
  echo "    Port: $PORT → $PORT"
  if [ "$NOADMIN" -eq 1 ]; then
    echo "    Mode: HF + No Admin (public endpoint simulation)"
  else
    echo "    Mode: HF + Admin enabled"
  fi
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  # Build the command
  CMD=(docker run
    --name "$CONTAINER_NAME"
    -p "$PORT:$PORT"
    --memory="12g"
    --memory-swap="12g"
    --cpus="2"
  )

  # Admin mode override via env var
  if [ "$NOADMIN" -eq 1 ]; then
    CMD+=(-e "ADMIN_MODE=0")
  fi

  CMD+=("$IMAGE_NAME")

  # Run in foreground (Ctrl+C to stop)
  echo ""
  echo "  Container logs (Ctrl+C to stop):"
  echo "  App will be available at: http://localhost:$PORT"
  echo ""
  "${CMD[@]}"
fi
