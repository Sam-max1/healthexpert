import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

model_id = "Qwen/Qwen2.5-1.5B-Instruct"

try:
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        rope_scaling={"type": "dynamic", "factor": 4.0}
    )
    print("Model loaded with dynamic rope scaling.")
    print("Context config:", model.config.max_position_embeddings)
except Exception as e:
    print("Error:", e)
