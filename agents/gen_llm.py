# gen_llm.py
# General-purpose LLM Generation Server — port 8002
#
# Modes:
#   GPU (default) : Qwen/Qwen3-8B, 4-bit NF4 bitsandbytes quantisation, ~5.5 GB VRAM
#   HF (CPU)      : Qwen/Qwen2.5-1.5B-Instruct, fp32, ~3 GB RAM, no quantisation
#
# Exposes: POST /v1/completions  (OpenAI-compatible)
#          POST /v1/kv_cache     (precompile KV cache — GPU mode only)
#          GET  /health
#
# Run: python agents/gen_llm.py
#      → http://127.0.0.1:8002

from __future__ import annotations

import os
os.environ["PYTHONWARNINGS"]   = "ignore"
os.environ["PYTORCH_ALLOC_CONF"] = "expandable_segments:True"
os.environ["TRANSFORMERS_VERBOSITY"] = "error"

import logging
import torch
from flask import Flask, request, jsonify
from transformers import AutoModelForCausalLM, AutoTokenizer

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("gen_llm")
logging.getLogger("werkzeug").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("filelock").setLevel(logging.WARNING)

# ── Config ────────────────────────────────────────────────────────────────────
HF_MODE    = os.getenv("HF_MODE",    "0") == "1"
MODEL_NAME = os.getenv("GEN_MODEL_ID", "Qwen/Qwen2.5-1.5B-Instruct" if HF_MODE else "Qwen/Qwen3-8B")
HOST       = os.getenv("GEN_HOST",  "127.0.0.1")
PORT       = int(os.getenv("GEN_PORT", "8002"))
SKIP_COMPILE = os.getenv("TORCH_COMPILE_SKIP", "0") == "1" or HF_MODE

log.info("━" * 60)
log.info("gen_llm starting — mode=%s  model=%s", "HF/CPU" if HF_MODE else "GPU", MODEL_NAME)
log.info("━" * 60)

# ── Device / GPU Detection ────────────────────────────────────────────────────
if not HF_MODE and torch.cuda.is_available():
    _cuda_count      = torch.cuda.device_count()
    _cuda_idx        = int(os.getenv("GEN_CUDA_DEVICE", "0"))
    _cuda_idx        = min(_cuda_idx, _cuda_count - 1)
    _device          = torch.device(f"cuda:{_cuda_idx}")
    _gpu_id          = f"cuda:{_cuda_idx}"
    _device_map      = {"": _cuda_idx}

    _gpu_props       = torch.cuda.get_device_properties(_cuda_idx)
    _vram_total      = _gpu_props.total_memory / 1024 ** 3
    torch.cuda.empty_cache()
    _vram_free       = torch.cuda.mem_get_info(_cuda_idx)[0] / 1024 ** 3
    _vram_budget_gib = max(4.0, _vram_total * 0.78)
    _max_memory      = {_cuda_idx: f"{_vram_budget_gib:.1f}GiB", "cpu": "4GiB"}

    log.info("GPU DETECTED")
    log.info("  Device : %s (cuda:%d)", _gpu_props.name, _cuda_idx)
    log.info("  VRAM   : %.2f GiB total / %.2f GiB free", _vram_total, _vram_free)
    log.info("  Budget : %.2f GiB (78%% cap — prevents OOM kill)", _vram_budget_gib)
    log.info("  Compute: %d.%d", _gpu_props.major, _gpu_props.minor)
else:
    _device     = torch.device("cpu")
    _gpu_id     = "cpu"
    _device_map = "cpu"
    _max_memory = None
    _cuda_idx   = None
    if HF_MODE:
        log.info("HF MODE — running on CPU (expected, no GPU required)")
    else:
        log.warning("NO GPU DETECTED — inference will run on CPU (slow).")

# ── Model Initialisation ───────────────────────────────────────────────────────
try:
    log.info("Loading tokenizer: %s ...", MODEL_NAME)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True)

    if _gpu_id == "cpu":
        # CPU path: plain fp32, no quantisation, no bitsandbytes dependency
        log.info("Loading model %s in fp32 (CPU mode) ...", MODEL_NAME)
        load_kwargs = dict(
            device_map=_device_map,
            trust_remote_code=True,
            torch_dtype=torch.float32,
        )
    else:
        # GPU path: 4-bit NF4 bitsandbytes quantisation
        log.info("Loading model %s with 4-bit NF4 quantisation → %s ...", MODEL_NAME, _gpu_id)
        # Guard import — bitsandbytes requires CUDA; never imported on CPU
        from transformers import BitsAndBytesConfig
        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
        load_kwargs = dict(
            quantization_config=bnb_config,
            device_map=_device_map,
            trust_remote_code=True,
        )

    if _max_memory is not None:
        load_kwargs["max_memory"] = _max_memory

    model = AutoModelForCausalLM.from_pretrained(MODEL_NAME, **load_kwargs)
    model.eval()

    if tokenizer.pad_token_id is None:
        tokenizer.pad_token_id = tokenizer.eos_token_id

except Exception as e:
    log.error("Failed to load model: %s", e)
    raise

log.info("Model ready! (%s, device=%s)", MODEL_NAME, _gpu_id)

# ── Optional torch.compile() (GPU only, skip in HF mode) ─────────────────────
_compile_applied = False
if not SKIP_COMPILE:
    try:
        import torch._dynamo
        torch._dynamo.config.suppress_errors = True
        model = torch.compile(model, mode="reduce-overhead", fullgraph=False)
        _compile_applied = True
        log.info("torch.compile() applied — repeated inference will be faster.")
    except Exception as _ce:
        log.info("torch.compile() skipped (%s) — running in eager mode.", type(_ce).__name__)
else:
    log.info("torch.compile() skipped (HF/CPU mode).")

# ── Flask app & KV Cache ───────────────────────────────────────────────────────
precompiled_kv_cache = None
kv_cache_length      = 0

app = Flask(__name__)


@app.route("/v1/kv_cache", methods=["POST"])
def kv_cache():
    """Precompile KV cache for Knowledge Base text.
    Disabled in HF mode to conserve RAM — returns a no-op response.
    """
    global precompiled_kv_cache, kv_cache_length

    # HF mode: KV cache disabled — acknowledge gracefully and return
    if HF_MODE:
        log.info("KV cache precompilation skipped (HF mode — RAM conservation).")
        return jsonify({"status": "skipped", "reason": "HF mode — KV cache disabled"})

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
    kv_cache_length      = inputs.input_ids.shape[1]

    return jsonify({
        "status": "cached",
        "tokens": kv_cache_length,
        "chars":  len(kb_text),
    })


@app.route("/health", methods=["GET"])
def health():
    """Liveness probe — includes RAM stats for the resource banner."""
    import psutil
    mem         = psutil.virtual_memory()
    ram_used_gb = round((mem.total - mem.available) / 1024 ** 3, 2)
    ram_total_gb = round(mem.total / 1024 ** 3, 2)

    vram_free_gib = 0.0
    if torch.cuda.is_available() and _cuda_idx is not None:
        try:
            vram_free_gib = round(torch.cuda.mem_get_info(_cuda_idx)[0] / 1024 ** 3, 2)
        except Exception:
            pass

    return jsonify({
        "status":          "ok",
        "model":           MODEL_NAME,
        "hf_mode":         HF_MODE,
        "gpu_id":          _gpu_id,
        "kv_cache_length": kv_cache_length,
        "kv_cache_enabled": not HF_MODE,
        "torch_compile":   _compile_applied,
        "vram_free_gib":   vram_free_gib,
        "ram_used_gb":     ram_used_gb,
        "ram_total_gb":    ram_total_gb,
    })


@app.route("/v1/completions", methods=["POST"])
def completions():
    """OpenAI-compatible completions endpoint."""
    data: dict = request.get_json(force=True) or {}

    raw_prompt = data.get("prompt", "")
    if not raw_prompt:
        return jsonify({"error": "Field 'prompt' is required."}), 400

    prompts: list[str] = raw_prompt if isinstance(raw_prompt, list) else [raw_prompt]

    # In HF mode cap max_new_tokens to conserve RAM / keep latency reasonable
    _default_max = 1024 if HF_MODE else 2048
    max_new_tokens = int(  data.get("max_tokens",   _default_max))
    temperature    = float(data.get("temperature",  0.7))
    top_p          = float(data.get("top_p",        0.95))
    thinking_mode  = bool( data.get("thinking_mode", False))
    use_kv_cache   = bool( data.get("use_kv_cache",  False)) and not HF_MODE

    choices = []
    total_prompt_tokens     = 0
    total_completion_tokens = 0

    for i, prompt in enumerate(prompts):
        if use_kv_cache and precompiled_kv_cache is not None:
            # KV-cache fast path (GPU mode only)
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
                    min_new_tokens=10,
                    temperature=temperature if temperature > 0.15 else 1.0,
                    top_p=top_p,
                    do_sample=temperature > 0.15,
                    repetition_penalty=1.1,
                    pad_token_id=tokenizer.pad_token_id,
                    eos_token_id=eos_token_id,
                    use_cache=True,
                )

            new_tokens  = outputs[0][curr_length:]
            answer_text = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
            prompt_len  = curr_length + kv_cache_length
            completion_len = len(new_tokens)

        else:
            # Standard path (HF CPU mode + GPU fallback without KV cache)
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
                    min_new_tokens=10,
                    temperature=temperature if temperature > 0.15 else 1.0,
                    top_p=top_p,
                    do_sample=temperature > 0.15,
                    repetition_penalty=1.1,
                    pad_token_id=tokenizer.pad_token_id,
                    eos_token_id=eos_token_id,
                    use_cache=True,
                )

            new_tokens     = outputs[0][inputs.input_ids.shape[1]:]
            answer_text    = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
            prompt_len     = inputs.input_ids.shape[1]
            completion_len = len(new_tokens)

        total_prompt_tokens     += prompt_len
        total_completion_tokens += completion_len

        # Strip <think>...</think> block (Qwen3 thinking mode)
        full_output = answer_text
        think_text  = ""
        if thinking_mode and "<think>" in full_output:
            think_end = full_output.find("</think>")
            if think_end != -1:
                think_text  = full_output[len("<think>"):think_end].strip()
                answer_text = full_output[think_end + len("</think>"):].strip()

        log.info("Prompt %d → %d new tokens (device=%s, hf_mode=%s)",
                 i, completion_len, _gpu_id, HF_MODE)

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
        },
    })


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import signal, sys

    def sigint_handler(sig, frame):
        log.info("SIGINT received — shutting down gen_llm gracefully...")
        sys.exit(0)
    signal.signal(signal.SIGINT, sigint_handler)

    log.info("Starting gen_llm server on %s:%d (HTTP, loopback only)", HOST, PORT)
    # Internal microservice — always plain HTTP.
    # SSL is handled exclusively by app.py at the browser-facing layer.
    # Using HTTPS here causes "Connection reset by peer" because app.py
    # connects via http:// (config.LLM_BASE_URL) to an HTTPS server.
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
