#!/bin/bash
# scripts/cleanup.sh — Kill all HealthExpert background services and free ports
#
# Usage:
#   bash scripts/cleanup.sh          # Kill all services (ports 8002, 8003, 5050, 7860)
#   bash scripts/cleanup.sh --quiet  # Suppress output (for use inside start.sh)
#
# Ports freed:
#   8002 → gen_llm.py    (LLM generation server)
#   8003 → embed_llm.py  (embedding server)
#   5050 → app.py        (Flask, desktop mode)
#   7860 → app.py        (Flask, HF Spaces mode)

QUIET=0
for arg in "$@"; do
  [ "$arg" = "--quiet" ] && QUIET=1
done

log() { [ "$QUIET" -eq 0 ] && echo "$1"; }

PORTS=(8002 8003 5050 7860)
PROCESS_PATTERNS=(
  "agents/gen_llm.py"
  "agents/embed_llm.py"
  "app.py"
)

KILLED=0

log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "  HealthExpert — Cleanup: freeing ports and killing services"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Step 1: Kill by port using fuser (most reliable) ──────────────────────────
for port in "${PORTS[@]}"; do
  if command -v fuser &>/dev/null; then
    PIDS=$(fuser "${port}/tcp" 2>/dev/null)
    if [ -n "$PIDS" ]; then
      log "  [port $port] Killing PIDs: $PIDS"
      fuser -k "${port}/tcp" 2>/dev/null && KILLED=$((KILLED + 1))
    else
      log "  [port $port] Free ✓"
    fi
  else
    # Fallback: lsof (macOS / systems without fuser)
    PIDS=$(lsof -t -i:"${port}" 2>/dev/null)
    if [ -n "$PIDS" ]; then
      log "  [port $port] Killing PIDs: $PIDS"
      echo "$PIDS" | xargs kill -9 2>/dev/null && KILLED=$((KILLED + 1))
    else
      log "  [port $port] Free ✓"
    fi
  fi
done

# ── Step 2: Kill by process name (catch orphans that changed ports) ────────────
for pattern in "${PROCESS_PATTERNS[@]}"; do
  PIDS=$(pgrep -f "$pattern" 2>/dev/null)
  if [ -n "$PIDS" ]; then
    log "  [process] Killing '$pattern' (PIDs: $PIDS)"
    pkill -9 -f "$pattern" 2>/dev/null && KILLED=$((KILLED + 1))
  fi
done

# ── Step 3: Brief pause to let OS release sockets ─────────────────────────────
if [ "$KILLED" -gt 0 ]; then
  log "  Waiting 2s for sockets to release..."
  sleep 2
fi

# ── Step 4: Verify ports are free ─────────────────────────────────────────────
log ""
ALL_FREE=1
for port in "${PORTS[@]}"; do
  if command -v fuser &>/dev/null; then
    STILL=$(fuser "${port}/tcp" 2>/dev/null)
  else
    STILL=$(lsof -t -i:"${port}" 2>/dev/null)
  fi
  if [ -n "$STILL" ]; then
    log "  ⚠ Port $port still in use (PID: $STILL) — may need manual kill"
    ALL_FREE=0
  fi
done

if [ "$ALL_FREE" -eq 1 ]; then
  log "  ✅ All ports free. Ready to restart."
else
  log "  ⚠  Some ports still busy. Try: sudo bash scripts/cleanup.sh"
fi

log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
exit 0
