import sys, os
os.environ["CUDA_VISIBLE_DEVICES"] = ""
from huggingface_hub import hf_hub_download
from llama_cpp import Llama

model_path = hf_hub_download(repo_id="Jackrong/Qwen3.5-4B-Claude-4.6-Opus-Reasoning-Distilled-GGUF", filename="Qwen3.5-4B.Q4_K_M.gguf")

try:
    llm = Llama(model_path=model_path, n_ctx=20000, n_gpu_layers=0)
    print("Success 20k")
except Exception as e:
    print(f"Failed 20k: {e}")
