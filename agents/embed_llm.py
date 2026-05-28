# embed_llm.py
# General-purpose Embedding Server — port 8003
#
# Model card reference: https://huggingface.co/BAAI/bge-m3
# Uses FlagEmbedding.BGEM3FlagModel for dense, sparse, and ColBERT embeddings.
#
# Exposes: POST /v1/embeddings       (OpenAI-compatible, dense vectors)
#          POST /v1/embeddings/multi  (dense + sparse + ColBERT scores)
#          GET  /health
#
# Run: python agents/embed_llm.py
#      → http://127.0.0.1:8003

from __future__ import annotations

import os
import logging

import numpy as np
from flask import Flask, request, jsonify
from FlagEmbedding import BGEM3FlagModel

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [embed_llm] %(levelname)s %(message)s",
)
log = logging.getLogger("embed_llm")


# ── JSON serialisation helper ─────────────────────────────────────────────────
def to_python(obj):
    """Recursively convert numpy/torch objects to plain Python for jsonify."""
    import numpy as np
    if isinstance(obj, dict):
        return {k: to_python(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_python(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.floating, np.float16, np.float32, np.float64)):
        return float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    try:  # torch.Tensor fallback
        import torch
        if isinstance(obj, torch.Tensor):
            return obj.cpu().detach().float().item() if obj.numel() == 1 else obj.cpu().detach().float().tolist()
    except ImportError:
        pass
    return obj

# ── Config ────────────────────────────────────────────────────────────────────
MODEL_NAME   = os.getenv("EMBED_MODEL_ID", "BAAI/bge-m3")
USE_FP16     = os.getenv("EMBED_FP16",     "true").lower() == "true"
MAX_LENGTH   = int(os.getenv("EMBED_MAX_LENGTH", "8192"))
BATCH_SIZE   = int(os.getenv("EMBED_BATCH_SIZE", "12"))
HOST         = os.getenv("EMBED_HOST",     "127.0.0.1")
PORT         = int(os.getenv("EMBED_PORT", "8003"))

# ── Model singleton ───────────────────────────────────────────────────────────
# BGEM3FlagModel is loaded once at startup — matches the model card usage pattern.
# Setting use_fp16=True speeds up computation with a slight performance degradation.

log.info("Loading model: %s  (fp16=%s)...", MODEL_NAME, USE_FP16)
model = BGEM3FlagModel(MODEL_NAME, use_fp16=USE_FP16)
log.info("Model ready: %s", MODEL_NAME)

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    """Liveness probe — returns model name and status."""
    return jsonify({"status": "ok", "model": MODEL_NAME, "fp16": USE_FP16})


# ── /v1/embeddings  (OpenAI-compatible, dense vectors only) ───────────────────

@app.route("/v1/embeddings", methods=["POST"])
def embeddings():
    """
    OpenAI-compatible dense-embedding endpoint.

    Request body (JSON):
        {
            "input": str | list[str],   # required — text(s) to embed
            "model": str                # optional, ignored (model fixed at startup)
        }

    Response body (JSON):
        {
            "object": "list",
            "model":  str,
            "data": [
                {"object": "embedding", "index": 0, "embedding": [float, ...]},
                ...
            ]
        }

    Dense Embedding (model card reference):
        embeddings = model.encode(sentences, batch_size=12, max_length=8192)['dense_vecs']
    """
    data: dict = request.get_json(force=True) or {}
    raw_input = data.get("input", "")
    if not raw_input:
        return jsonify({"error": "Field 'input' is required."}), 400

    sentences: list[str] = raw_input if isinstance(raw_input, list) else [raw_input]

    try:
        # ── Dense Embedding — as per model card ────────────────────────────────
        output = model.encode(
            sentences,
            batch_size  = BATCH_SIZE,
            max_length  = MAX_LENGTH,
        )
        dense_vecs = output["dense_vecs"]          # ndarray (N, dim)
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


# ── /v1/embeddings/multi  (dense + sparse + ColBERT) ─────────────────────────

@app.route("/v1/embeddings/multi", methods=["POST"])
def embeddings_multi():
    """
    Multi-vector embedding endpoint — returns dense, sparse (lexical), and ColBERT vectors.

    Request body (JSON):
        {
            "sentences_1": list[str],   # required — first set of sentences
            "sentences_2": list[str],   # optional — second set for scoring
            "weights":     list[float]  # optional — [dense_w, sparse_w, colbert_w] default [0.4,0.2,0.4]
        }

    Response body (JSON):
        {
            "dense_similarity":    [[float]],   # dot product similarity matrix
            "lexical_weights_1":   [dict],      # sparse token weights for sentences_1
            "lexical_weights_2":   [dict],      # sparse token weights for sentences_2
            "colbert_scores":      [[float]],   # ColBERT max-sim scores (pairs)
            "combined_scores":     [float]      # weighted combination if sentences_2 provided
        }

    Model card references:
        Dense:   output['dense_vecs']
        Sparse:  output['lexical_weights']
        ColBERT: output['colbert_vecs']
        Scores:  model.compute_score(pairs, weights_for_different_modes=[0.4, 0.2, 0.4])
    """
    data: dict = request.get_json(force=True) or {}
    sentences_1: list[str] = data.get("sentences_1", [])
    sentences_2: list[str] = data.get("sentences_2", [])
    weights: list[float]   = data.get("weights", [0.4, 0.2, 0.4])

    if not sentences_1:
        return jsonify({"error": "Field 'sentences_1' is required."}), 400

    try:
        # ── Encode both sets with all vector types ─────────────────────────────
        output_1 = model.encode(
            sentences_1,
            return_dense=True, return_sparse=True, return_colbert_vecs=True,
            batch_size=BATCH_SIZE, max_length=MAX_LENGTH,
        )

        response: dict = {}

        # Dense similarity matrix (sentences_1 × sentences_1 by default)
        dense_vecs_1 = output_1["dense_vecs"]
        response["dense_similarity"] = (dense_vecs_1 @ dense_vecs_1.T).tolist()

        # Lexical weights — convert token IDs to readable tokens
        response["lexical_weights_1"] = [
            model.convert_id_to_token(lw) for lw in output_1["lexical_weights"]
        ]

        if sentences_2:
            output_2 = model.encode(
                sentences_2,
                return_dense=True, return_sparse=True, return_colbert_vecs=True,
                batch_size=BATCH_SIZE, max_length=MAX_LENGTH,
            )
            dense_vecs_2 = output_2["dense_vecs"]

            # Cross-similarity matrix
            response["dense_similarity"] = (dense_vecs_1 @ dense_vecs_2.T).tolist()

            # Lexical weights for set 2
            response["lexical_weights_2"] = [
                model.convert_id_to_token(lw) for lw in output_2["lexical_weights"]
            ]

            # ColBERT scores — use float() which handles both scalar tensors and
            # multi-element tensors returned by colbert_score
            response["colbert_scores"] = [
                [
                    float(model.colbert_score(
                        output_1["colbert_vecs"][i],
                        output_2["colbert_vecs"][j],
                    ))
                    for j in range(len(sentences_2))
                ]
                for i in range(len(sentences_1))
            ]

            # Combined weighted scores — compute_score returns a dict whose
            # values are lists of numpy floats; run through to_python() first.
            pairs = [[s1, s2] for s1 in sentences_1 for s2 in sentences_2]
            combined_raw = model.compute_score(
                pairs,
                max_passage_length          = MAX_LENGTH,
                weights_for_different_modes = weights,
            )
            response["combined_scores"] = to_python(combined_raw)

    except Exception as exc:
        log.exception("Multi-embedding failed")
        return jsonify({"error": str(exc)}), 500

    log.info(
        "Multi-embed: %d × %d sentences",
        len(sentences_1), len(sentences_2) if sentences_2 else 0,
    )
    return jsonify(to_python(response))


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Starting embed_llm server on %s:%d", HOST, PORT)
    log.info("Model: %s  fp16=%s  batch=%d  max_len=%d", MODEL_NAME, USE_FP16, BATCH_SIZE, MAX_LENGTH)
    app.run(host=HOST, port=PORT, debug=False, threaded=True)