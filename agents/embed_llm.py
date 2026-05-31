# embed_llm.py
# General-purpose Embedding Server — port 8003
#
# Modes:
#   GPU (default) : BAAI/bge-m3 via FlagEmbedding — dense, sparse, ColBERT
#   HF (CPU)      : BAAI/bge-small-en-v1.5 via sentence-transformers — dense only (~130 MB)
#
# Exposes: POST /v1/embeddings       (OpenAI-compatible, dense vectors)
#          POST /v1/embeddings/multi  (dense + sparse + ColBERT — GPU mode only)
#          GET  /health
#
# Run: python agents/embed_llm.py
#      → http://127.0.0.1:8003

from __future__ import annotations

import os
os.environ["PYTHONWARNINGS"] = "ignore"
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
HF_MODE    = os.getenv("HF_MODE",    "0") == "1"
MODEL_NAME = os.getenv("EMBED_MODEL_ID", "BAAI/bge-small-en-v1.5" if HF_MODE else "BAAI/bge-m3")
USE_FP16   = os.getenv("EMBED_FP16", "false" if HF_MODE else "true").lower() == "true"
MAX_LENGTH = int(os.getenv("EMBED_MAX_LENGTH", "512" if HF_MODE else "8192"))
BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "2"   if HF_MODE else "12"))
HOST       = os.getenv("EMBED_HOST", "127.0.0.1")
PORT       = int(os.getenv("EMBED_PORT", "8003"))

log.info("━" * 60)
log.info("embed_llm starting — mode=%s  model=%s", "HF/CPU" if HF_MODE else "GPU", MODEL_NAME)
log.info("━" * 60)

# ── Model Loading ─────────────────────────────────────────────────────────────
# HF mode  → sentence-transformers SentenceTransformer (lightweight, CPU-friendly)
# GPU mode → FlagEmbedding BGEM3FlagModel (dense + sparse + ColBERT)

_use_st_model = HF_MODE or MODEL_NAME != "BAAI/bge-m3"

if _use_st_model:
    log.info("Loading SentenceTransformer model: %s ...", MODEL_NAME)
    from sentence_transformers import SentenceTransformer
    _st_model   = SentenceTransformer(MODEL_NAME)
    _bge_model  = None
    # get_embedding_dimension() is the new name (sentence-transformers ≥ 3.x)
    # Fall back to get_sentence_embedding_dimension() for older installs
    _get_dim = getattr(_st_model, "get_embedding_dimension",
                       _st_model.get_sentence_embedding_dimension)
    _embed_dim  = _get_dim()
    log.info("SentenceTransformer model ready — dim=%d", _embed_dim)
else:
    log.info("Loading BGEM3FlagModel: %s  (fp16=%s) ...", MODEL_NAME, USE_FP16)
    from FlagEmbedding import BGEM3FlagModel
    _bge_model = BGEM3FlagModel(MODEL_NAME, use_fp16=USE_FP16)
    _st_model  = None
    log.info("BGEM3FlagModel ready: %s", MODEL_NAME)


def _embed_sentences(sentences: list[str]) -> np.ndarray:
    """Embed a list of sentences and return dense vectors as ndarray (N, dim)."""
    if _st_model is not None:
        vecs = _st_model.encode(
            sentences,
            batch_size=BATCH_SIZE,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        return vecs if isinstance(vecs, np.ndarray) else np.array(vecs)
    else:
        output = _bge_model.encode(
            sentences,
            batch_size=BATCH_SIZE,
            max_length=MAX_LENGTH,
        )
        return output["dense_vecs"]


# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    """Liveness probe — returns model name, mode, and status."""
    return jsonify({
        "status":   "ok",
        "model":    MODEL_NAME,
        "hf_mode":  HF_MODE,
        "fp16":     USE_FP16,
        "backend":  "sentence-transformers" if _use_st_model else "FlagEmbedding/bge-m3",
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


# ── /v1/embeddings/multi  (dense + sparse + ColBERT — GPU mode only) ─────────

@app.route("/v1/embeddings/multi", methods=["POST"])
def embeddings_multi():
    """
    Multi-vector embedding endpoint (GPU/bge-m3 mode only).
    In HF mode returns a friendly error directing callers to /v1/embeddings.
    """
    if _use_st_model:
        return jsonify({
            "error": "Multi-vector embeddings require bge-m3 (GPU mode). "
                     "Use /v1/embeddings for dense-only embeddings in HF mode."
        }), 501

    data: dict = request.get_json(force=True) or {}
    sentences_1: list[str] = data.get("sentences_1", [])
    sentences_2: list[str] = data.get("sentences_2", [])
    weights: list[float]   = data.get("weights", [0.4, 0.2, 0.4])

    if not sentences_1:
        return jsonify({"error": "Field 'sentences_1' is required."}), 400

    try:
        output_1 = _bge_model.encode(
            sentences_1,
            return_dense=True, return_sparse=True, return_colbert_vecs=True,
            batch_size=BATCH_SIZE, max_length=MAX_LENGTH,
        )
        response: dict = {}
        dense_vecs_1 = output_1["dense_vecs"]
        response["dense_similarity"] = (dense_vecs_1 @ dense_vecs_1.T).tolist()
        response["lexical_weights_1"] = [
            _bge_model.convert_id_to_token(lw) for lw in output_1["lexical_weights"]
        ]

        if sentences_2:
            output_2 = _bge_model.encode(
                sentences_2,
                return_dense=True, return_sparse=True, return_colbert_vecs=True,
                batch_size=BATCH_SIZE, max_length=MAX_LENGTH,
            )
            dense_vecs_2 = output_2["dense_vecs"]
            response["dense_similarity"] = (dense_vecs_1 @ dense_vecs_2.T).tolist()
            response["lexical_weights_2"] = [
                _bge_model.convert_id_to_token(lw) for lw in output_2["lexical_weights"]
            ]
            response["colbert_scores"] = [
                [
                    float(_bge_model.colbert_score(
                        output_1["colbert_vecs"][i],
                        output_2["colbert_vecs"][j],
                    ))
                    for j in range(len(sentences_2))
                ]
                for i in range(len(sentences_1))
            ]
            pairs = [[s1, s2] for s1 in sentences_1 for s2 in sentences_2]
            combined_raw = _bge_model.compute_score(
                pairs,
                max_passage_length=MAX_LENGTH,
                weights_for_different_modes=weights,
            )
            response["combined_scores"] = to_python(combined_raw)

    except Exception as exc:
        log.exception("Multi-embedding failed")
        return jsonify({"error": str(exc)}), 500

    log.info("Multi-embed: %d × %d sentences", len(sentences_1), len(sentences_2) if sentences_2 else 0)
    return jsonify(to_python(response))


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import signal, sys

    def sigint_handler(sig, frame):
        log.info("SIGINT received — shutting down embed_llm gracefully...")
        sys.exit(0)
    signal.signal(signal.SIGINT, sigint_handler)

    log.info("Starting embed_llm server on %s:%d (HTTP, loopback only)", HOST, PORT)
    log.info("Model: %s  backend=%s  fp16=%s  batch=%d  max_len=%d",
             MODEL_NAME,
             "sentence-transformers" if _use_st_model else "FlagEmbedding",
             USE_FP16, BATCH_SIZE, MAX_LENGTH)
    # Internal microservice — always plain HTTP.
    # SSL is handled exclusively by app.py at the browser-facing layer.
    # Using HTTPS here causes "Connection reset by peer" because app.py
    # connects via http:// (config.EMBED_BASE_URL) to an HTTPS server.
    app.run(host=HOST, port=PORT, debug=False, threaded=True)