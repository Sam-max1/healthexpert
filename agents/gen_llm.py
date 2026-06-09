# gen_llm.py
# General-purpose LLM Generation Server — port 8002
#
# Modes:
#   GPU & HF (CPU) : Jackrong/Qwen3.5-4B-Claude-4.6-Opus-Reasoning-Distilled-GGUF, ~2.5 GB RAM/VRAM
#
# Multi-user concurrency model:
#   A single inference worker thread owns all model.create_chat_completion calls.
#   HTTP requests enqueue a job (data + result holder + done_event) and block
#   until their result is ready.  If the queue is full or the request times out
#   the endpoint returns 503 {"error": "Server busy — try again shortly."} so
#   the caller can retry without hanging indefinitely.
#
# Exposes: POST /v1/completions  (OpenAI-compatible)
#          POST /v1/kv_cache     (precompile KV cache — GPU mode only)
#          GET  /health          (includes queue_depth for the UI busy badge)
#
# Run: python agents/gen_llm.py
#      → http://127.0.0.1:8002

from __future__ import annotations

import os
import warnings
warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"]    = "ignore"
os.environ["CUDA_VISIBLE_DEVICES"] = ""  # Force hide GPU from llama.cpp so it doesn't crash on CUDA buffer allocation in HF mode
os.environ["LLAMA_NUMA"] = "1"           # Enable NUMA optimizations

import logging
import threading
import queue as _queue_module
import time
from flask import Flask, request, jsonify

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("gen_llm")
logging.getLogger("werkzeug").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ── Config ────────────────────────────────────────────────────────────────────
HF_MODE      = True  # Hardcoded to True to permanently disable GPU for HF execution
MODEL_REPO   = os.getenv("GEN_MODEL_ID", "Jackrong/Qwen3.5-4B-Claude-4.6-Opus-Reasoning-Distilled-GGUF")
MODEL_FILE   = os.getenv("GEN_MODEL_FILENAME", "Qwen3.5-4B.Q4_K_M.gguf")
HOST         = os.getenv("GEN_HOST",  "127.0.0.1")
PORT         = int(os.getenv("GEN_PORT", "8002"))

# Maximum number of requests that can wait in the inference queue.
_QUEUE_MAX_SIZE    = int(os.getenv("GEN_QUEUE_MAX", "8"))

# Per-request timeout (seconds).  Matches config.py LLM_TIMEOUT default.
_REQUEST_TIMEOUT_S = int(os.getenv("LLM_TIMEOUT", "600"))

log.info("━" * 60)
log.info("gen_llm starting — mode=%s  model=%s (%s)", "HF/CPU" if HF_MODE else "GPU", MODEL_REPO, MODEL_FILE)
log.info("Queue: max_size=%d  request_timeout=%ds", _QUEUE_MAX_SIZE, _REQUEST_TIMEOUT_S)
log.info("━" * 60)

# ── Device / GPU Detection ────────────────────────────────────────────────────
_n_gpu_layers = 0
_gpu_id = "cpu"

if not HF_MODE:
    try:
        import torch
        if torch.cuda.is_available():
            _cuda_idx = int(os.getenv("GEN_CUDA_DEVICE", "0"))
            _gpu_id = f"cuda:{_cuda_idx}"
            _n_gpu_layers = -1
            log.info("GPU DETECTED — Offloading all layers to %s", _gpu_id)
    except ImportError:
        pass
else:
    log.info("HF MODE — running on CPU (expected, no GPU required)")


# ── Model Initialisation ───────────────────────────────────────────────────────
_model_ready = threading.Event()
model = None

try:
    log.info("Downloading/Locating model from Hub: %s/%s", MODEL_REPO, MODEL_FILE)
    from huggingface_hub import hf_hub_download
    model_path = hf_hub_download(repo_id=MODEL_REPO, filename=MODEL_FILE)
    
    log.info("Loading GGUF model via llama.cpp ...")
    cpu_threads = 2
    log.info("Llama Init: threads=%d, n_ctx=8192, n_batch=512, numa=True, flash_attn=True", cpu_threads)
    
    from llama_cpp import Llama
    model = Llama(
        model_path=model_path,
        n_ctx=8192,         # 8k context to reduce memory overhead and speed up prefill
        n_batch=512,
        n_threads=cpu_threads,
        n_gpu_layers=_n_gpu_layers,
        use_mmap=True,
        use_mlock=True,
        numa=True,
        flash_attn=True,
        verbose=False,
    )

except Exception as e:
    log.error("Failed to load model: %s", e)
    raise

log.info("Model ready! (%s, device=%s)", MODEL_REPO, _gpu_id)

# ── Flask app & KV Cache ───────────────────────────────────────────────────────
precompiled_kv_cache = None
kv_cache_length      = 0

app = Flask(__name__)


# ── Inference Queue (multi-user serialization) ─────────────────────────────────
_inference_queue: _queue_module.Queue = _queue_module.Queue(maxsize=_QUEUE_MAX_SIZE)


def _run_inference(data: dict) -> dict:
    """Execute one completion request.  Called only from the inference worker thread."""
    raw_prompt = data.get("prompt", "")
    if not raw_prompt:
        return {"error": "Field 'prompt' is required."}

    prompts = raw_prompt if isinstance(raw_prompt, list) and (len(raw_prompt) == 0 or not isinstance(raw_prompt[0], dict)) else [raw_prompt]

    _default_max = 1024 if HF_MODE else 2048
    max_new_tokens = int(  data.get("max_tokens",   _default_max))
    temperature    = float(data.get("temperature",  0.7))
    top_p          = float(data.get("top_p",        0.95))
    thinking_mode  = bool( data.get("thinking_mode", False))

    choices = []
    total_prompt_tokens     = 0
    total_completion_tokens = 0

    for i, prompt in enumerate(prompts):
        if isinstance(prompt, list):
            messages = prompt
        else:
            messages = [
                {"role": "system", "content": "You are a helpful, respectful and honest assistant."},
                {"role": "user", "content": prompt}
            ]
            
        # Call chat completion API
        outputs = model.create_chat_completion(
            messages=messages,
            max_tokens=max_new_tokens,
            temperature=temperature if temperature > 0.15 else 0.0,
            top_p=top_p,
        )

        answer_text = outputs["choices"][0]["message"]["content"].strip()
        prompt_len = outputs["usage"]["prompt_tokens"]
        completion_len = outputs["usage"]["completion_tokens"]

        total_prompt_tokens     += prompt_len
        total_completion_tokens += completion_len

        # Strip <think>...</think> block
        full_output = answer_text
        think_text  = ""
        think_end = full_output.find("</think>")
        if think_end != -1:
            if "<think>" in full_output:
                think_start = full_output.find("<think>")
                think_text  = full_output[think_start + len("<think>"):think_end].strip()
            else:
                think_text  = full_output[:think_end].strip()
            answer_text = full_output[think_end + len("</think>"):].strip()

        log.info("Prompt %d → %d new tokens (device=%s, hf_mode=%s)",
                 i, completion_len, _gpu_id, HF_MODE)

        choices.append({
            "index":    i,
            "text":     answer_text,
            "thinking": think_text,
        })

    return {
        "model":   MODEL_REPO,
        "choices": choices,
        "usage": {
            "prompt_tokens":     total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
        },
    }


def _inference_worker() -> None:
    log.info("Inference worker thread started (pid=%d)", os.getpid())
    _model_ready.set()

    while True:
        try:
            item = _inference_queue.get(timeout=1.0)
        except _queue_module.Empty:
            continue

        req_data, result_holder, done_event = item
        try:
            result_holder[0] = _run_inference(req_data)
        except Exception as exc:
            log.error("Inference worker error: %s", exc)
            result_holder[0] = {"error": f"Inference failed: {exc}"}
        finally:
            done_event.set()
            _inference_queue.task_done()


_worker_thread = threading.Thread(target=_inference_worker, name="inference-worker", daemon=True)
_worker_thread.start()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/v1/kv_cache", methods=["POST"])
def kv_cache():
    """KV cache is managed natively by llama.cpp. This is a no-op."""
    return jsonify({"status": "skipped", "reason": "llama.cpp manages KV cache natively"})


@app.route("/health", methods=["GET"])
def health():
    import psutil
    mem          = psutil.virtual_memory()
    ram_used_gb  = round((mem.total - mem.available) / 1024 ** 3, 2)
    ram_total_gb = round(mem.total / 1024 ** 3, 2)

    queue_depth = _inference_queue.qsize()
    is_ready    = _model_ready.is_set()

    return jsonify({
        "status":           "ok" if is_ready else "loading",
        "model":            MODEL_REPO,
        "hf_mode":          HF_MODE,
        "gpu_id":           _gpu_id,
        "kv_cache_length":  0,
        "kv_cache_enabled": False,
        "torch_compile":    False,
        "vram_free_gib":    0.0,
        "ram_used_gb":      ram_used_gb,
        "ram_total_gb":     ram_total_gb,
        "queue_depth":      queue_depth,
        "queue_max":        _QUEUE_MAX_SIZE,
        "model_ready":      is_ready,
    })


@app.route("/v1/completions", methods=["POST"])
def completions():
    if not _model_ready.is_set():
        return jsonify({
            "error":       "Model is still loading — please try again in a few seconds.",
            "retry_after": 5,
        }), 503

    data: dict = request.get_json(force=True) or {}

    current_depth = _inference_queue.qsize()
    if current_depth >= _QUEUE_MAX_SIZE:
        log.warning("Inference queue full (%d/%d) — rejecting request.", current_depth, _QUEUE_MAX_SIZE)
        return jsonify({
            "error":       "Server busy — all inference slots are occupied. Please try again shortly.",
            "retry_after": max(5, current_depth * 3),
            "queue_depth": current_depth,
            "queue_max":   _QUEUE_MAX_SIZE,
        }), 503

    result_holder: list = [None]
    done_event = threading.Event()

    try:
        _inference_queue.put_nowait((data, result_holder, done_event))
    except _queue_module.Full:
        return jsonify({
            "error":       "Server busy — inference queue full. Please try again shortly.",
            "retry_after": 5,
        }), 503

    completed = done_event.wait(timeout=_REQUEST_TIMEOUT_S)

    if not completed:
        return jsonify({
            "error":       f"Request timed out after {_REQUEST_TIMEOUT_S}s. ",
            "retry_after": 10,
        }), 503

    result = result_holder[0]
    if result is None:
        return jsonify({"error": "Internal error: inference worker returned no result."}), 500

    if "error" in result:
        return jsonify(result), 500

    return jsonify(result)


if __name__ == "__main__":
    import signal, sys

    def sigint_handler(sig, frame):
        sys.exit(0)
    signal.signal(signal.SIGINT, sigint_handler)

    app.run(host=HOST, port=PORT, debug=False, threaded=True)
