import os
import logging
import threading
import queue as _queue_module
import time
import requests
import base64
from flask import Flask, request, jsonify

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("nvidia_llm")
logging.getLogger("werkzeug").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ── Config ────────────────────────────────────────────────────────────────────
HOST         = os.getenv("NVIDIA_HOST",  "127.0.0.1")
PORT         = int(os.getenv("NVIDIA_PORT", "8004"))
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")

_QUEUE_MAX_SIZE    = int(os.getenv("NVIDIA_QUEUE_MAX", "8"))
_REQUEST_TIMEOUT_S = int(os.getenv("NVIDIA_LLM_TIMEOUT", "600"))

DEFAULT_MODEL = "google/diffusiongemma-26b-a4b-it"

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    return response

# ── Inference Queue (multi-user serialization) ─────────────────────────────────
_inference_queue: _queue_module.Queue = _queue_module.Queue(maxsize=_QUEUE_MAX_SIZE)

if not NVIDIA_API_KEY:
    log.warning("NVIDIA_API_KEY is not set. API calls will fail unless the key is provided.")


def _run_inference(data: dict) -> dict:
    raw_prompt = data.get("prompt", "")
    if not raw_prompt:
        return {"error": "Field 'prompt' is required."}

    prompts = raw_prompt if isinstance(raw_prompt, list) and (len(raw_prompt) == 0 or not isinstance(raw_prompt[0], dict)) else [raw_prompt]

    # F4/F21: Dual LLM mode routing
    llm_mode = data.get("llm_mode", "expert")
    if llm_mode == "assistant":
        model_name = "minimaxai/minimax-m3"
        max_tokens = int(data.get("max_tokens", 8192))
        chat_template_kwargs = None
        temperature = float(data.get("temperature", 1.0))
        top_p = float(data.get("top_p", 0.95))
    else:
        model_name = data.get("model", DEFAULT_MODEL)
        max_tokens = int(data.get("max_tokens", 4096))
        chat_template_kwargs = {"enable_thinking": True}
        temperature = float(data.get("temperature", 1.00))
        top_p = float(data.get("top_p", 0.95))

    invoke_url = "https://integrate.api.nvidia.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json"
    }

    choices = []
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    for i, prompt in enumerate(prompts):
        if isinstance(prompt, list):
            messages = prompt
        else:
            messages = [{"role": "user", "content": prompt}]

        try:
            print(f"\n[NVIDIA] Generating via {model_name}")
            print("-" * 30)

            payload = {
                "model": model_name,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "top_p": top_p,
                "stream": False,
            }
            if chat_template_kwargs:
                payload["chat_template_kwargs"] = chat_template_kwargs

            response = requests.post(invoke_url, headers=headers, json=payload, timeout=_REQUEST_TIMEOUT_S)
            response.raise_for_status()
            resp_data = response.json()

            usage = resp_data.get("usage", {})
            total_usage["prompt_tokens"]     += usage.get("prompt_tokens", 0)
            total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
            total_usage["total_tokens"]      += usage.get("total_tokens", 0)

            full_output = ""
            if "choices" in resp_data and len(resp_data["choices"]) > 0:
                message = resp_data["choices"][0].get("message", {})
                if message.get("content"):
                    full_output = message["content"]
                    print(full_output)

            print("\n" + "-" * 30)

            choices.append({
                "index": i,
                "text": full_output.strip(),
                "thinking": ""
            })

        except Exception as e:
            log.error(f"Error calling NVIDIA API: {e}")
            choices.append({
                "index": i,
                "text": f"Error: {str(e)}",
                "thinking": ""
            })

    return {
        "model": model_name,
        "choices": choices,
        "usage": total_usage,
        "device": "cloud_nvidia"
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

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "queue_depth": _inference_queue.qsize(),
        "queue_max": _QUEUE_MAX_SIZE
    })


@app.route("/v1/completions", methods=["POST", "OPTIONS"])
def completions():
    if request.method == "OPTIONS":
        return jsonify({}), 200

    data: dict = request.get_json(force=True) or {}

    current_depth = _inference_queue.qsize()
    if current_depth >= _QUEUE_MAX_SIZE:
        return jsonify({
            "error": "Server busy — all inference slots are occupied. Please try again shortly.",
            "retry_after": 5
        }), 503

    result_holder: list = [None]
    done_event = threading.Event()

    try:
        _inference_queue.put_nowait((data, result_holder, done_event))
    except _queue_module.Full:
        return jsonify({
            "error": "Server busy — inference queue full. Please try again shortly.",
            "retry_after": 5,
        }), 503

    completed = done_event.wait(timeout=_REQUEST_TIMEOUT_S)

    if not completed:
        return jsonify({
            "error": f"Request timed out after {_REQUEST_TIMEOUT_S}s.",
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

    log.info(f"Starting NVIDIA LLM agent on http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
