# gen_llm.py
# General-purpose LLM Generation Server — port 8002
#
# Modes:
#   CPU  : llama.cpp on CPU (default; works without GPU)
#   GPU  : llama.cpp with GPU layers (enabled per-request via use_gpu=true)
#
# Multi-user concurrency model:
#   A single inference worker thread owns all model.create_chat_completion calls.
#   HTTP requests enqueue a job (data + result holder + done_event) and block
#   until their result is ready.  If the queue is full or the request times out
#   the endpoint returns 503 {"error": "Server busy — try again shortly."} so
#   the caller can retry without hanging indefinitely.
#
# Exposes: POST /v1/completions  (OpenAI-compatible)
#          POST /v1/kv_cache     (no-op — llama.cpp manages natively)
#          GET  /health          (includes queue_depth for the UI busy badge)
#
# GPU/CPU control:
#   - Pass use_gpu=true in the request body to run on GPU (if available).
#   - Pass cpu_threads=N in the request body to override thread count (CPU mode).
#   - If GPU is unavailable, use_gpu is silently ignored.
#
# Run: python agents/gen_llm.py
#      → http://127.0.0.1:8002

from __future__ import annotations

import os
import warnings
warnings.filterwarnings("ignore")
os.environ["PYTHONWARNINGS"]    = "ignore"
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
MODEL_REPO   = os.getenv("GEN_MODEL_ID", "Jackrong/Qwen3.5-2B-Claude-4.6-Opus-Reasoning-Distilled-GGUF")
MODEL_FILE   = os.getenv("GEN_MODEL_FILENAME", "Qwen3.5-2B.Q4_K_M.gguf")
HOST         = os.getenv("GEN_HOST",  "127.0.0.1")
PORT         = int(os.getenv("GEN_PORT", "8002"))

# Default CPU thread count (overridable per-request)
DEFAULT_CPU_THREADS = int(os.getenv("GEN_CPU_THREADS", "2"))

# Maximum number of requests that can wait in the inference queue.
_QUEUE_MAX_SIZE    = int(os.getenv("GEN_QUEUE_MAX", "8"))

# Per-request timeout (seconds).  Matches config.py LLM_TIMEOUT default.
_REQUEST_TIMEOUT_S = int(os.getenv("LLM_TIMEOUT", "600"))

# ── Device / GPU Detection ────────────────────────────────────────────────────
_gpu_available = False
_gpu_id = "cpu"

try:
    import torch
    if torch.cuda.is_available():
        _cuda_idx = int(os.getenv("GEN_CUDA_DEVICE", "0"))
        _gpu_id = f"cuda:{_cuda_idx}"
        _gpu_available = True
        log.info("GPU DETECTED — %s available for on-demand inference", _gpu_id)
    else:
        log.info("No CUDA GPU detected — CPU-only inference available")
except ImportError:
    log.info("torch not available — GPU detection skipped, CPU-only mode")

# Do NOT set CUDA_VISIBLE_DEVICES="" here — we need GPU access to be possible.
# GPU layers are set per-model-instance (see _load_model below).

log.info("━" * 60)
log.info("gen_llm starting — gpu_available=%s  model=%s (%s)", _gpu_available, MODEL_REPO, MODEL_FILE)
log.info("Default CPU threads=%d  Queue: max_size=%d  request_timeout=%ds",
         DEFAULT_CPU_THREADS, _QUEUE_MAX_SIZE, _REQUEST_TIMEOUT_S)
log.info("━" * 60)


# ── Model Loading ─────────────────────────────────────────────────────────────
# We maintain up to two model instances: CPU and (optionally) GPU.
# This avoids full model reload on every request while allowing GPU offload.

_model_lock  = threading.Lock()
_models: dict[str, object] = {}   # key: "cpu" or "gpu"
_model_ready = threading.Event()
_model_path  = None


def _load_model(use_gpu: bool = False):
    """Load and cache a model instance. Returns the cached instance if already loaded."""
    mode_key = "gpu" if (use_gpu and _gpu_available) else "cpu"
    
    with _model_lock:
        if mode_key in _models:
            return _models[mode_key]
        
        global _model_path
        if _model_path is None:
            log.info("Downloading/Locating model from Hub: %s/%s", MODEL_REPO, MODEL_FILE)
            from huggingface_hub import hf_hub_download
            _model_path = hf_hub_download(repo_id=MODEL_REPO, filename=MODEL_FILE)
        
        n_gpu_layers = -1 if (use_gpu and _gpu_available) else 0
        cpu_threads  = DEFAULT_CPU_THREADS
        
        log.info("Loading GGUF model — mode=%s  n_gpu_layers=%d  threads=%d", 
                 mode_key, n_gpu_layers, cpu_threads)
        
        from llama_cpp import Llama
        m = Llama(
            model_path=_model_path,
            n_ctx=8192,
            n_batch=512,
            n_threads=cpu_threads,
            n_gpu_layers=n_gpu_layers,
            use_mmap=True,
            use_mlock=True,
            numa=True,
            flash_attn=True,
            verbose=False,
        )
        _models[mode_key] = m
        log.info("Model instance [%s] ready!", mode_key)
        return m


# Pre-load CPU model at startup (always available)
try:
    _load_model(use_gpu=False)
    _model_ready.set()
    log.info("CPU model pre-loaded and ready.")
except Exception as e:
    log.error("Failed to load CPU model: %s", e)
    raise


# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)

# ── Inference Queue (multi-user serialization) ─────────────────────────────────
_inference_queue: _queue_module.Queue = _queue_module.Queue(maxsize=_QUEUE_MAX_SIZE)


def _run_inference(data: dict) -> dict:
    """Execute one completion request.  Called only from the inference worker thread."""
    raw_prompt = data.get("prompt", "")
    if not raw_prompt:
        return {"error": "Field 'prompt' is required."}

    prompts = raw_prompt if isinstance(raw_prompt, list) and (len(raw_prompt) == 0 or not isinstance(raw_prompt[0], dict)) else [raw_prompt]

    use_gpu     = bool(data.get("use_gpu", False))
    cpu_threads = int(data.get("cpu_threads", DEFAULT_CPU_THREADS))

    # Select model instance (GPU if requested and available, else CPU)
    model = _load_model(use_gpu=use_gpu)
    active_device = _gpu_id if (use_gpu and _gpu_available) else "cpu"

    # Apply cpu_threads override if running on CPU and different from default
    # Note: llama_cpp doesn't support live thread changes; we log the intent.
    if not (use_gpu and _gpu_available) and cpu_threads != DEFAULT_CPU_THREADS:
        log.info("cpu_threads=%d requested (model loaded with %d — static per-instance)", 
                 cpu_threads, DEFAULT_CPU_THREADS)

    _default_max = 1024
    max_new_tokens  = int(  data.get("max_tokens",        _default_max))
    
    # Reasoning models need large token budgets for internal monologue.
    # Enforce a minimum of 2048 tokens to prevent truncation, unless 
    # explicitly asking for very small probe tests (<100 tokens).
    if 100 < max_new_tokens < 2048:
        max_new_tokens = 2048
    temperature     = float(data.get("temperature",       0.7))
    top_p           = float(data.get("top_p",             0.95))
    top_k           = int(  data.get("top_k",             40))
    repeat_penalty  = float(data.get("repeat_penalty",    1.15))
    freq_penalty    = float(data.get("frequency_penalty", 0.1))

    choices = []
    total_prompt_tokens     = 0
    total_completion_tokens = 0

    # ── Sliding-window repetition detector (mirrors ai_workbench) ──
    REP_WINDOW    = 120   # characters to treat as one "phrase"
    REP_THRESHOLD = 2     # how many duplicate occurrences to tolerate

    def _is_repeating(text: str) -> bool:
        if len(text) < REP_WINDOW * (REP_THRESHOLD + 1):
            return False
        tail = text[-REP_WINDOW:]
        preceding = text[: -REP_WINDOW]
        count = 0
        start = 0
        while True:
            idx = preceding.find(tail, start)
            if idx == -1:
                break
            count += 1
            if count >= REP_THRESHOLD:
                return True
            start = idx + 1
        return False

    for i, prompt in enumerate(prompts):
        if isinstance(prompt, list):
            messages = prompt
        else:
            messages = [
                {"role": "system", "content": "You are a helpful, respectful and honest assistant."},
                {"role": "user", "content": prompt}
            ]

        # Call chat completion API using streaming with full sampling controls
        stream = model.create_chat_completion(
            messages=messages,
            max_tokens=max_new_tokens,
            temperature=temperature if temperature > 0.15 else 0.0,
            top_p=top_p,
            top_k=top_k,
            repeat_penalty=repeat_penalty,
            frequency_penalty=freq_penalty,
            stream=True,
        )

        full_output = ""
        prompt_len = len(str(messages)) // 4
        completion_len = 0
        
        print(f"\n[CONSOLE STREAM] Generating for: {MODEL_REPO}")
        print("-" * 30)

        for chunk in stream:
            if "choices" in chunk and len(chunk["choices"]) > 0:
                choice = chunk["choices"][0]
                text_part = choice.get("delta", {}).get("content", "")
                if not text_part:
                    text_part = choice.get("text", "") # fallback if delta not present
                    
                if text_part:
                    print(text_part, end="", flush=True)
                    full_output += text_part
                    completion_len += 1

                if _is_repeating(full_output):
                    print("\n[CONSOLE STREAM] Repetition detected — cutting off generation.")
                    full_output = full_output[:-REP_WINDOW].strip()
                    break

        print("\n" + "-" * 30)

        answer_text = full_output.strip()

        total_prompt_tokens     += prompt_len
        total_completion_tokens += completion_len

        # Strip <think>...</think> block robustly (handles 4 failure modes)
        think_text  = ""
        think_end = answer_text.find("</think>")
        think_start = answer_text.find("<think>")
        
        if think_end != -1:
            # Case 1: Both <think> and </think> present
            if think_start != -1 and think_start < think_end:
                think_text = answer_text[think_start + len("<think>"):think_end].strip()
                answer_text = (answer_text[:think_start] + "\n" + answer_text[think_end + len("</think>"):]).strip()
            else:
                # Case 2: Only </think> found — model started thinking implicitly
                think_text = answer_text[:think_end].strip()
                answer_text = answer_text[think_end + len("</think>"):].strip()
        elif think_start != -1:
            # Case 3: Orphaned <think> with NO </think> — model exhausted tokens mid-thought
            think_text = answer_text[think_start + len("<think>"):].strip()
            answer_text = answer_text[:think_start].strip()
        
        # Case 4: No tags at all — detect untagged thinking patterns from tiny models
        if not answer_text or (not think_text and answer_text):
            _THINK_PREFIXES = (
                "Thinking Process:", "Let me analyze", "Let me think",
                "I need to analyze", "Let me break this down",
                "Let me review", "Let me examine", "Let me consider",
                "I'll analyze", "Step 1:", "1.  **Analyze",
            )
            stripped = answer_text.lstrip("\n ")
            for prefix in _THINK_PREFIXES:
                if stripped.startswith(prefix):
                    think_text = stripped
                    answer_text = ""
                    break

        log.info("Prompt %d → %d new tokens (device=%s, gpu=%s, threads=%d)",
                 i, completion_len, active_device, use_gpu and _gpu_available, cpu_threads)

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
        "device": active_device,
    }


def _inference_worker() -> None:
    log.info("Inference worker thread started (pid=%d)", os.getpid())

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
    loaded_modes = list(_models.keys())

    return jsonify({
        "status":           "ok" if is_ready else "loading",
        "model":            MODEL_REPO,
        "gpu_available":    _gpu_available,
        "gpu_id":           _gpu_id,
        "loaded_modes":     loaded_modes,
        "default_threads":  DEFAULT_CPU_THREADS,
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
