import time
from llama_cpp import Llama
import os

model_path = "/source/python/code/models/hub/models--Jackrong--Qwen3.5-4B-Claude-4.6-Opus-Reasoning-Distilled-GGUF/snapshots/cd70250b528abda79bc0390af5031c40e0a73edc/Qwen3.5-4B.Q4_K_M.gguf"

for threads in [4, 8, 16, 32, 64]:
    print(f"\n--- Testing with {threads} threads ---")
    start_load = time.time()
    llm = Llama(
        model_path=model_path,
        n_ctx=512,
        n_batch=512,
        n_threads=threads,
        use_mmap=True,
        use_mlock=False, # to load faster for test
        numa=True,
        flash_attn=True,
        verbose=False
    )
    load_time = time.time() - start_load
    print(f"Loaded in {load_time:.2f}s")
    
    start_gen = time.time()
    output = llm("The capital of France is", max_tokens=64)
    gen_time = time.time() - start_gen
    
    tok = output['usage']['completion_tokens']
    print(f"Generated {tok} tokens in {gen_time:.2f}s -> {tok/gen_time:.2f} tok/sec")
    del llm
