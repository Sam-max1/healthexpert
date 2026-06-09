# embed_llm.py
# General-purpose Embedding Server — port 8003
#
# Modes:
#   GPU & HF (CPU) : BAAI/bge-small-en-v1.5 via sentence-transformers — dense only (~130 MB)
#
# Exposes: POST /v1/embeddings       (OpenAI-compatible, dense vectors)
#          GET  /health
#
# Run: python agents/embed_llm.py
#      → http://127.0.0.1:8003

from __future__ import annotations

import os
os.environ["PYTHONWARNINGS"] = "ignore"
os.environ["TORCH_LOGS"] = "-all"
os.environ["NUMEXPR_MAX_THREADS"] = "16"
import logging

import numpy as np
from flask import Flask, request, jsonify

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("embed_llm")
logging.getLogger("werkzeug").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("filelock").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("numexpr").setLevel(logging.ERROR)

# ── JSON serialisation helper ─────────────────────────────────────────────────
def to_python(obj):
    """Recursively convert numpy/torch objects to plain Python for jsonify."""
    if isinstance(obj, dict):
        return {k: to_python(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_python(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.floating, np.float16, np.float32, np.float64)):
        return float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    try:
        import torch
        if isinstance(obj, torch.Tensor):
            return obj.cpu().detach().float().item() if obj.numel() == 1 else obj.cpu().detach().float().tolist()
    except ImportError:
        pass
    return obj


# ── Config ────────────────────────────────────────────────────────────────────
HF_MODE    = True  # Hardcoded to True to permanently disable GPU for HF execution
MODEL_NAME = os.getenv("EMBED_MODEL_ID", "BAAI/bge-small-en-v1.5")
MAX_LENGTH = int(os.getenv("EMBED_MAX_LENGTH", "512"))
BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "12"))
HOST       = os.getenv("EMBED_HOST", "127.0.0.1")
PORT       = int(os.getenv("EMBED_PORT", "8003"))

log.info("━" * 60)
log.info("embed_llm starting — mode=%s  model=%s", "HF/CPU" if HF_MODE else "GPU", MODEL_NAME)
log.info("━" * 60)

# ── Model Loading ─────────────────────────────────────────────────────────────
# GPU & HF mode  → sentence-transformers SentenceTransformer (lightweight, CPU-friendly)

log.info("Loading SentenceTransformer model: %s ...", MODEL_NAME)
from sentence_transformers import SentenceTransformer
_st_model   = SentenceTransformer(MODEL_NAME)
# get_embedding_dimension() is the new name (sentence-transformers ≥ 3.x)
# Fall back to get_sentence_embedding_dimension() for older installs
_get_dim = getattr(_st_model, "get_embedding_dimension",
                   _st_model.get_sentence_embedding_dimension)
_embed_dim  = _get_dim()
log.info("SentenceTransformer model ready — dim=%d", _embed_dim)


def _embed_sentences(sentences: list[str]) -> np.ndarray:
    """Embed a list of sentences and return dense vectors as ndarray (N, dim)."""
    vecs = _st_model.encode(
        sentences,
        batch_size=BATCH_SIZE,
        show_progress_bar=False,
        normalize_embeddings=True,
    )
    return vecs if isinstance(vecs, np.ndarray) else np.array(vecs)


# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    """Liveness probe — returns model name, mode, and status."""
    return jsonify({
        "status":   "ok",
        "model":    MODEL_NAME,
        "hf_mode":  HF_MODE,
        "backend":  "sentence-transformers",
    })


# ── /v1/embeddings  (OpenAI-compatible, dense vectors) ───────────────────────

@app.route("/v1/embeddings", methods=["POST"])
def embeddings():
    """
    OpenAI-compatible dense-embedding endpoint.

    Request body (JSON):
        { "input": str | list[str] }

    Response body (JSON):
        { "object": "list", "model": str,
          "data": [{"object": "embedding", "index": int, "embedding": [float, ...]}, ...] }
    """
    data: dict = request.get_json(force=True) or {}
    raw_input = data.get("input", "")
    if not raw_input:
        return jsonify({"error": "Field 'input' is required."}), 400

    sentences: list[str] = raw_input if isinstance(raw_input, list) else [raw_input]

    try:
        dense_vecs = _embed_sentences(sentences)
    except Exception as exc:
        log.exception("Embedding failed")
        return jsonify({"error": str(exc)}), 500

    result_data = [
        {
            "object":    "embedding",
            "index":     i,
            "embedding": vec.tolist() if isinstance(vec, np.ndarray) else list(vec),
        }
        for i, vec in enumerate(dense_vecs)
    ]

    log.info("Embedded %d sentence(s), dim=%d", len(sentences), len(result_data[0]["embedding"]))
    return jsonify({"object": "list", "model": MODEL_NAME, "data": result_data})


# ── /v1/embeddings/multi  (deprecated) ───────────────────────────────────────
@app.route("/v1/embeddings/multi", methods=["POST"])
def embeddings_multi():
    return jsonify({
        "error": "Multi-vector embeddings require bge-m3 (GPU mode). "
                 "Use /v1/embeddings for dense-only embeddings."
    }), 501


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import signal, sys

    def sigint_handler(sig, frame):
        log.info("SIGINT received — shutting down embed_llm gracefully...")
        sys.exit(0)
    signal.signal(signal.SIGINT, sigint_handler)

    log.info("Starting embed_llm server on %s:%d (HTTP, loopback only)", HOST, PORT)
    log.info("Model: %s  backend=sentence-transformers  batch=%d  max_len=%d",
             MODEL_NAME, BATCH_SIZE, MAX_LENGTH)
    # Internal microservice — always plain HTTP.
    # SSL is handled exclusively by app.py at the browser-facing layer.
    # Using HTTPS here causes "Connection reset by peer" because app.py
    # connects via http:// (config.EMBED_BASE_URL) to an HTTPS server.
    app.run(host=HOST, port=PORT, debug=False, threaded=True)