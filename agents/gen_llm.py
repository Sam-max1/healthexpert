# gen_llm.py
# General-purpose LLM Generation Server — port 8002
#
# Model: Qwen/Qwen3-8B  (4-bit NF4 bitsandbytes quantization, ~5.5 GB VRAM)
#
# Exposes: POST /v1/completions  (OpenAI-compatible)
#          POST /v1/kv_cache     (precompile KV cache for KB text)
#          GET  /health
#
# Run: python agents/gen_llm.py
#      → https://127.0.0.1:8002

from __future__ import annotations

import os
os.environ["PYTHONWARNINGS"] = "ignore"
os.environ["PYTORCH_ALLOC_CONF"] = "expandable_segments:True"
os.environ['TRANSFORMERS_VERBOSITY'] = 'error'

import logging
import torch
from flask import Flask, request, jsonify
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("gen_llm")
logging.getLogger("werkzeug").setLevel(logging.ERROR)

# ── Config ────────────────────────────────────────────────────────────────────
MODEL_NAME = os.getenv("GEN_MODEL_ID", "Qwen/Qwen3-8B")
HOST       = os.getenv("GEN_HOST",    "127.0.0.1")
PORT       = int(os.getenv("GEN_PORT", "8002"))

# ── GPU Detection ─────────────────────────────────────────────────────────────
# Probe CUDA availability and select the target device before model load.
# If GPU is found, inference is forced onto it.  Falls back to CPU with a
# warning so the server still starts (slowly) when no GPU is present.

if torch.cuda.is_available():
    _cuda_count = torch.cuda.device_count()
    _cuda_idx   = int(os.getenv("GEN_CUDA_DEVICE", "0"))   # override via env if needed
    _cuda_idx   = min(_cuda_idx, _cuda_count - 1)           # clamp to valid range
    _device     = torch.device(f"cuda:{_cuda_idx}")
    _gpu_id     = f"cuda:{_cuda_idx}"
    _device_map = {"":  _cuda_idx}                          # force ALL layers onto this GPU

    _gpu_props  = torch.cuda.get_device_properties(_cuda_idx)
    _vram_total = _gpu_props.total_memory / 1024 ** 3       # bytes → GiB
    _vram_free  = (torch.cuda.mem_get_info(_cuda_idx)[0]) / 1024 ** 3

    log.info("━" * 60)
    log.info("GPU DETECTED")
    log.info("  Device index : %d of %d", _cuda_idx, _cuda_count)
    log.info("  Device name  : %s", _gpu_props.name)
    log.info("  CUDA version : %s", torch.version.cuda)
    log.info("  VRAM total   : %.2f GiB", _vram_total)
    log.info("  VRAM free    : %.2f GiB", _vram_free)
    log.info("  Compute cap  : %d.%d", _gpu_props.major, _gpu_props.minor)
    log.info("  Inference    : FORCED on %s", _gpu_id)
    log.info("━" * 60)
else:
    _device     = torch.device("cpu")
    _gpu_id     = "cpu"
    _device_map = "cpu"
    log.warning("━" * 60)
    log.warning("NO GPU DETECTED — inference will run on CPU (slow).")
    log.warning("Ensure CUDA drivers and PyTorch CUDA build are installed.")
    log.warning("━" * 60)

# ── 4-bit NF4 quantization (bitsandbytes) ────────────────────────────────────
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.bfloat16,
    bnb_4bit_use_double_quant=True,     # nested quantization saves ~0.4 GB additional
)

# ── Model Initialization ───────────────────────────────────────────────────────
log.info("Loading model %s with 4-bit NF4 quantization → %s ...", MODEL_NAME, _gpu_id)
try:
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_NAME,
        quantization_config=bnb_config,
        device_map=_device_map,     # explicitly mapped to detected GPU (or CPU)
        trust_remote_code=True,
    )
    model.eval()

    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id

except Exception as e:
    log.error("Failed to load model: %s", e)
    raise

log.info("Model ready! (%s, 4-bit NF4, device=%s)", MODEL_NAME, _gpu_id)

# ── Flask app & KV Cache ──────────────────────────────────────────────────────
precompiled_kv_cache = None
kv_cache_length = 0

app = Flask(__name__)

@app.route("/v1/kv_cache", methods=["POST"])
def kv_cache():
    global precompiled_kv_cache, kv_cache_length
    data: dict = request.get_json(force=True) or {}
    kb_text = data.get("text", "")
    if not kb_text:
        precompiled_kv_cache = None
        kv_cache_length = 0
        return jsonify({"status": "cleared"})

    log.info("Precompiling KV Cache for Knowledge Base (%d chars)", len(kb_text))

    # Qwen ChatML system prompt prefix
    prefix = (
        f"<|im_start|>system\nYou are a helpful, respectful and honest assistant.\n\n"
        f"Here is the entire Knowledge Base:\n{kb_text}\nUnderstood?<|im_end|>\n"
        f"<|im_start|>user\nAcknowledge the Knowledge Base.<|im_end|>\n"
        f"<|im_start|>assistant\nUnderstood."
    )

    inputs = tokenizer(prefix, return_tensors="pt").to(model.device)
    with torch.no_grad():
        outputs = model(**inputs, use_cache=True)

    precompiled_kv_cache = outputs.past_key_values
    kv_cache_length = inputs.input_ids.shape[1]

    return jsonify({
        "status": "cached",
        "tokens": kv_cache_length,
        "chars": len(kb_text)
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":           "ok",
        "model":            MODEL_NAME,
        "quantization":     "4-bit NF4 (bitsandbytes)",
        "gpu_id":           _gpu_id,
        "kv_cache_length":  kv_cache_length,
    })


@app.route("/v1/completions", methods=["POST"])
def completions():
    """OpenAI-compatible completions endpoint."""
    data: dict = request.get_json(force=True) or {}

    raw_prompt = data.get("prompt", "")
    if not raw_prompt:
        return jsonify({"error": "Field 'prompt' is required."}), 400

    prompts: list[str] = raw_prompt if isinstance(raw_prompt, list) else [raw_prompt]

    max_new_tokens = int(  data.get("max_tokens",   2048))
    temperature    = float(data.get("temperature",  0.7))
    top_p          = float(data.get("top_p",        0.95))
    thinking_mode  = bool( data.get("thinking_mode", False))
    use_kv_cache   = bool( data.get("use_kv_cache",  False))

    choices = []
    total_prompt_tokens = 0
    total_completion_tokens = 0

    for i, prompt in enumerate(prompts):
        if use_kv_cache and precompiled_kv_cache is not None:
            # System prompt already in KV cache — only pass the user turn
            text = f"\n<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"

            inputs = tokenizer(text, return_tensors="pt").to(model.device)
            curr_length = inputs.input_ids.shape[1]
            attention_mask = torch.ones(
                (1, kv_cache_length + curr_length), device=model.device
            )

            eos_token_id = tokenizer.encode("<|im_end|>", add_special_tokens=False)[0]
            with torch.no_grad():
                outputs = model.generate(
                    input_ids=inputs.input_ids,
                    attention_mask=attention_mask,
                    past_key_values=precompiled_kv_cache,
                    max_new_tokens=max_new_tokens,
                    temperature=temperature if temperature > 0 else 1.0,
                    top_p=top_p,
                    do_sample=temperature > 0,
                    pad_token_id=tokenizer.pad_token_id,
                    eos_token_id=eos_token_id,
                )

            new_tokens = outputs[0][curr_length:]
            answer_text = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

            prompt_len = curr_length + kv_cache_length
            completion_len = len(new_tokens)

        else:
            text = (
                f"<|im_start|>system\nYou are a helpful, respectful and honest assistant."
                f"<|im_end|>\n<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
            )

            inputs = tokenizer(text, return_tensors="pt").to(model.device)
            eos_token_id = tokenizer.encode("<|im_end|>", add_special_tokens=False)[0]

            with torch.no_grad():
                outputs = model.generate(
                    input_ids=inputs.input_ids,
                    attention_mask=inputs.attention_mask,
                    max_new_tokens=max_new_tokens,
                    temperature=temperature if temperature > 0 else 1.0,
                    top_p=top_p,
                    do_sample=temperature > 0,
                    pad_token_id=tokenizer.pad_token_id,
                    eos_token_id=eos_token_id,
                )

            new_tokens = outputs[0][inputs.input_ids.shape[1]:]
            answer_text = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()

            prompt_len = inputs.input_ids.shape[1]
            completion_len = len(new_tokens)

        total_prompt_tokens     += prompt_len
        total_completion_tokens += completion_len

        # ── Separate <think> block from visible answer ─────────────────────
        full_output = answer_text
        think_text  = ""
        if thinking_mode and "<think>" in full_output:
            think_end = full_output.find("</think>")
            if think_end != -1:
                think_text  = full_output[len("<think>"):think_end].strip()
                answer_text = full_output[think_end + len("</think>"):].strip()

        log.debug("Prompt %d → %d new tokens (thinking=%s)", i, completion_len, thinking_mode)

        choices.append({
            "index":    i,
            "text":     answer_text,
            "thinking": think_text,
        })

    return jsonify({
        "model":   MODEL_NAME,
        "choices": choices,
        "usage": {
            "prompt_tokens":     total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
        }
    })


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import signal, sys

    def sigint_handler(sig, frame):
        log.info("SIGINT received, shutting down gen_llm gracefully...")
        sys.exit(0)
    signal.signal(signal.SIGINT, sigint_handler)

    log.info("Starting gen_llm server on %s:%d", HOST, PORT)
    cert_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cert.pem")
    key_path  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "key.pem")
    if os.path.exists(cert_path) and os.path.exists(key_path):
        app.run(host=HOST, port=PORT, debug=False, threaded=False,
                ssl_context=(cert_path, key_path))
    else:
        app.run(host=HOST, port=PORT, debug=False, threaded=False)
