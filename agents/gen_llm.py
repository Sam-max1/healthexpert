# gen_llm.py
# General-purpose LLM Generation Server — port 8002
#
# Model: Qwen/Qwen3-8B  (HuggingFace transformers)
# Model card: https://huggingface.co/Qwen/Qwen3-8B
#
# Exposes: POST /v1/completions  (OpenAI-compatible)
#          GET  /health
#
# Run: python agents/gen_llm.py
#      → http://127.0.0.1:8002

from __future__ import annotations

import os
import logging

import torch
from flask import Flask, request, jsonify
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [gen_llm] %(levelname)s %(message)s",
)
log = logging.getLogger("gen_llm")

# ── Config ────────────────────────────────────────────────────────────────────
MODEL_NAME = os.getenv("GEN_MODEL_ID", "Qwen/Qwen3-8B")
HOST       = os.getenv("GEN_HOST",    "127.0.0.1")
PORT       = int(os.getenv("GEN_PORT", "8002"))

# ── Device selection ──────────────────────────────────────────────────────────
DEVICE     = "cuda" if torch.cuda.is_available() else "cpu"
TORCH_DTYPE = torch.float16 if DEVICE == "cuda" else torch.float32

# ── Model singleton ───────────────────────────────────────────────────────────
# AutoModelForCausalLM + AutoTokenizer — standard Qwen3-8B model card pattern.
# Model is loaded once at startup and kept alive for the process lifetime.

log.info("Loading tokenizer: %s ...", MODEL_NAME)
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)  # nosec B615

log.info("Loading model: %s on %s (%s) — using 4-bit quantization...",
         MODEL_NAME, DEVICE, TORCH_DTYPE)

bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_compute_dtype=TORCH_DTYPE,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_use_double_quant=True,
)

model = AutoModelForCausalLM.from_pretrained(  # nosec B615
    MODEL_NAME,
    torch_dtype=TORCH_DTYPE,
    device_map="auto",
    quantization_config=bnb_config if DEVICE == "cuda" else None,
)
model.eval()
log.info("Model ready: %s", MODEL_NAME)

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
    
    prefix = f"<|im_start|>system\nYou are an expert analyst. Here is the entire Knowledge Base:\n{kb_text}<|im_end|>\n"
    model_inputs = tokenizer([prefix], return_tensors="pt").to(model.device)
    
    with torch.no_grad():
        outputs = model(**model_inputs, use_cache=True)
        precompiled_kv_cache = outputs.past_key_values
        kv_cache_length = model_inputs.input_ids.shape[-1]
        
    return jsonify({
        "status": "cached", 
        "tokens": kv_cache_length,
        "chars": len(kb_text)
    })



@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok", 
        "model": MODEL_NAME,
        "gpu_id": str(model.device),
        "kv_cache_length": kv_cache_length
    })


@app.route("/v1/completions", methods=["POST"])
def completions():
    """
    OpenAI-compatible completions endpoint.

    Follows the Qwen3-8B model card generation pattern:
        model.generate(**model_inputs, max_new_tokens=..., temperature=..., top_p=...)

    Request body (JSON):
        {
            "prompt":       str | list[str],  # required
            "max_tokens":   int,              # optional, default 2048
            "temperature":  float,            # optional, default 0.7
            "top_p":        float,            # optional, default 0.9
            "thinking_mode": bool             # optional, default false
                                              # true → enables Qwen3 chain-of-thought
        }

    Response body (JSON):
        {
            "model":   str,
            "choices": [{"index": 0, "text": str}]
        }
    """
    data: dict = request.get_json(force=True) or {}

    raw_prompt = data.get("prompt", "")
    if not raw_prompt:
        return jsonify({"error": "Field 'prompt' is required."}), 400

    # Accept a single string or a list (batch)
    prompts: list[str] = raw_prompt if isinstance(raw_prompt, list) else [raw_prompt]

    max_new_tokens = int(  data.get("max_tokens",   2048))
    temperature    = float(data.get("temperature",  0.7))
    top_p          = float(data.get("top_p",        0.9))
    thinking_mode  = bool( data.get("thinking_mode", False))
    use_kv_cache   = bool( data.get("use_kv_cache",  False))

    choices = []

    for i, prompt in enumerate(prompts):
        if use_kv_cache and precompiled_kv_cache is not None:
            text = f"<|im_start|>user\n{prompt}<|im_end|>\n<|im_start|>assistant\n"
            model_inputs = tokenizer([text], return_tensors="pt").to(model.device)
            current_len = model_inputs.input_ids.shape[-1]
            attention_mask = torch.ones((1, kv_cache_length + current_len), device=model.device, dtype=torch.long)
            position_ids = torch.arange(kv_cache_length, kv_cache_length + current_len, device=model.device).unsqueeze(0)
            cache_position = torch.arange(kv_cache_length, kv_cache_length + current_len, device=model.device)
            
            if hasattr(precompiled_kv_cache, "to_legacy_cache"):
                from transformers.cache_utils import DynamicCache
                past_kv = DynamicCache.from_legacy_cache(precompiled_kv_cache.to_legacy_cache())
            else:
                past_kv = precompiled_kv_cache
                
            with torch.no_grad():
                generated_ids = model.generate(
                    input_ids=model_inputs.input_ids,
                    attention_mask=attention_mask,
                    position_ids=position_ids,
                    cache_position=cache_position,
                    past_key_values=past_kv,
                    max_new_tokens=max_new_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    do_sample=True,
                )
        else:
            messages = [{"role": "user", "content": prompt}]
            text = tokenizer.apply_chat_template(
                messages,
                tokenize          = False,
                add_generation_prompt = True,
                enable_thinking   = thinking_mode,
            )
            model_inputs = tokenizer([text], return_tensors="pt").to(model.device)
            
            with torch.no_grad():
                generated_ids = model.generate(
                    **model_inputs,
                    max_new_tokens = max_new_tokens,
                    temperature    = temperature,
                    top_p          = top_p,
                    do_sample      = True,
                )

        # Strip the input tokens to return only the new text
        output_ids = generated_ids[0][model_inputs.input_ids.shape[-1]:]

        # ── Decode: separate <think> block from visible answer ─────────────
        # Qwen3 thinking mode wraps reasoning in <think>...</think> tags.
        # We surface both so callers can use either.
        full_output = tokenizer.decode(output_ids, skip_special_tokens=True).strip()

        think_text, answer_text = "", full_output
        if thinking_mode and "<think>" in full_output:
            think_end = full_output.find("</think>")
            if think_end != -1:
                think_text  = full_output[len("<think>"):think_end].strip()
                answer_text = full_output[think_end + len("</think>"):].strip()

        log.info("Prompt %d → %d new tokens (thinking=%s)", i, len(output_ids), thinking_mode)

        choices.append({
            "index":   i,
            "text":    answer_text,
            "thinking": think_text,   # empty string when thinking_mode=False
        })

    return jsonify({"model": MODEL_NAME, "choices": choices})


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Starting gen_llm server on %s:%d", HOST, PORT)
    log.info("Model: %s | Device: %s | CUDA: %s", MODEL_NAME, DEVICE, torch.cuda.is_available())
    # threaded=False — PyTorch CUDA is not fork-safe; requests are handled serially
    app.run(host=HOST, port=PORT, debug=False, threaded=False)
