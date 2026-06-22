#!/bin/bash
# start.sh — HealthExpert-HF service orchestrator
#
# Usage:
#   bash start.sh              # GPU mode using NVIDIA NIM cloud API
#   bash start.sh -hf          # HuggingFace low-resource mode (CPU, small models)
#   bash start.sh -hf -noadmin # HF mode with admin controls disabled (public endpoint)
#
# Environment variables set by this script:
#   HF_MODE=1       → Activates low-resource CPU path in config.py, embed_llm.py
#   ADMIN_MODE=0    → Disables admin API routes and hides UI admin controls
#   GEN_MODEL_ID    → Overridden for HF mode (small local GGUF)
#   EMBED_MODEL_ID  → Overridden for HF mode (bge-small-en-v1.5)

# NOTE: Do NOT use 'set -e' here — background processes exiting would abort the script.

# ── Parse CLI arguments ────────────────────────────────────────────────────────
HF_MODE_FLAG=0
ADMIN_MODE_FLAG=1

for arg in "$@"; do
  case "$arg" in
    -hf|--hf)           HF_MODE_FLAG=1 ;;
    -noadmin|--noadmin) ADMIN_MODE_FLAG=0 ;;
    *) ;;
  esac
done

# ── Export mode flags ──────────────────────────────────────────────────────────
export HF_MODE=$HF_MODE_FLAG
export ADMIN_MODE=$ADMIN_MODE_FLAG

# ── Mode-specific overrides ────────────────────────────────────────────────────
if [ "$HF_MODE_FLAG" -eq 1 ]; then
  export GEN_MODEL_ID="Jackrong/Qwen3.5-2B-Claude-4.6-Opus-Reasoning-Distilled-GGUF"
  export GEN_MODEL_FILENAME="Qwen3.5-2B.Q4_K_M.gguf"
  export EMBED_MODEL_ID="BAAI/bge-small-en-v1.5"
  export LLM_MAX_TOKENS=2048
  export EMBEDDING_BATCH_SIZE=2
  export TOP_K_VECTOR=3
  export TOP_K_GRAPH=3
  export EMBED_FP16=false          # CPU only — FP16 unsupported
  export TORCH_COMPILE_SKIP=1      # Skip torch.compile() on CPU (no benefit, adds 30s startup)
  MODE_LABEL="HuggingFace / CPU"
else
  export GEN_MODEL_ID="${GEN_MODEL_ID:-Jackrong/Qwen3.5-2B-Claude-4.6-Opus-Reasoning-Distilled-GGUF}"
  export GEN_MODEL_FILENAME="${GEN_MODEL_FILENAME:-Qwen3.5-2B.Q4_K_M.gguf}"
  export EMBED_MODEL_ID="${EMBED_MODEL_ID:-BAAI/bge-small-en-v1.5}"
  MODE_LABEL="GPU (Desktop)"
fi

if [ "$ADMIN_MODE_FLAG" -eq 0 ]; then
  ADMIN_LABEL="Admin: DISABLED (public mode)"
else
  ADMIN_LABEL="Admin: ENABLED"
fi

# ── Startup banner ─────────────────────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  HealthExpert — Starting"
echo "  Mode    : $MODE_LABEL"
echo "  $ADMIN_LABEL"
echo "  Gen LLM : $GEN_MODEL_ID"
echo "  Embed   : $EMBED_MODEL_ID"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Auto-cleanup: kill any stale services from a previous run ──────────────────
# This prevents "Address already in use" errors after a crash or incomplete shutdown.
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CLEANUP_SCRIPT="$SCRIPT_DIR/scripts/cleanup.sh"

if [ -f "$CLEANUP_SCRIPT" ]; then
  echo "[pre-flight] Cleaning up stale services on ports 8002, 8003, 5050, 7860..."
  bash "$CLEANUP_SCRIPT" --quiet
  echo "[pre-flight] Cleanup done."
else
  # Inline fallback if cleanup.sh is not present
  echo "[pre-flight] Freeing ports 8002, 8003, 5050, 7860..."
  for port in 8002 8003 5050 7860; do
    if command -v fuser &>/dev/null; then
      fuser -k "${port}/tcp" 2>/dev/null || true
    else
      lsof -t -i:"${port}" 2>/dev/null | xargs kill -9 2>/dev/null || true
    fi
  done
  # Also kill by process name for orphaned workers
  pkill -9 -f "agents/nvidia_llm.py" 2>/dev/null || true
  pkill -9 -f "agents/embed_llm.py"  2>/dev/null || true
  sleep 2
  echo "[pre-flight] Done."
fi

# ── Start gen_llm on port 8002 (local GGUF inference) ────────────────────────
echo "[1/4] Starting embed_llm (port 8003)..."
python agents/embed_llm.py &
EMBED_PID=$!
echo "      embed_llm PID: $EMBED_PID"

echo "[2/4] Starting gen_llm (local GGUF, port 8002)..."
python agents/gen_llm.py &
GEN_PID=$!
echo "      gen_llm PID: $GEN_PID"

# ── Start nvidia_llm on port 8004 (NVIDIA NIM cloud router) ──────────────────
# Runs in both HF and GPU modes. Requires NVIDIA_API_KEY env var.
# If NVIDIA_API_KEY is not set, nvidia_llm starts but returns errors on requests.
echo "[3/4] Starting nvidia_llm (NVIDIA NIM cloud, port 8004)..."
python agents/nvidia_llm.py &
NVIDIA_PID=$!
echo "      nvidia_llm PID: $NVIDIA_PID"

# ── Wait for microservices to initialise ──────────────────────────────────────
echo "Waiting for LLM microservices to initialise..."
if [ "$HF_MODE_FLAG" -eq 1 ]; then
  # CPU model load takes 30-60s; wait longer
  sleep 30
else
  sleep 5
fi

# ── Start Flask web application ─────────────────────────────────────────
echo "[4/4] Starting Flask web application (port ${PORT:-7860})..."
python app.py &
APP_PID=$!
echo "      app.py PID: $APP_PID"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  All services started."
echo "  Access: http://0.0.0.0:${PORT:-7860}"
echo "  PIDs  : embed=$EMBED_PID  gen=$GEN_PID  nvidia=$NVIDIA_PID  app=$APP_PID"
echo "  Ports : embed=8003  local_llm=8002  nvidia_llm=8004  app=${PORT:-7860}"
echo "  Stop  : bash scripts/cleanup.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Wait for any process to exit; clean up the rest ───────────────────────────
wait -n 2>/dev/null || wait
echo "[shutdown] A service exited. Running cleanup..."
bash "$CLEANUP_SCRIPT" --quiet 2>/dev/null || true
exit 0
