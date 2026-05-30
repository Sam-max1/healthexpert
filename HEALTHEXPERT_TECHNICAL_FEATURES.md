# HealthExpert: Technical Features & Edge Architecture

HealthExpert is an engineering marvel designed to compress enterprise-grade Agentic RAG (Retrieval-Augmented Generation) into a footprint small enough to run on consumer-grade, disconnected hardware.

---

## 1. On-Device Agentic Orchestration

Running multi-agent frameworks (like CrewAI) typically requires pinging cloud APIs (OpenAI, Anthropic) dozens of times per query. HealthExpert eliminates this dependency:

- **Local Inference Engine (`gen_llm`):** A heavily optimized, asynchronous Flask service that wraps a quantized Small Language Model (SLM) or Large Language Model (LLM).
- **Zero-Roundtrip ReAct:** We have bypassed standard CrewAI ReAct loops for the synthesis phase, replacing 3 redundant LLM calls with 1 direct, highly focused prompt. This reduces query latency from ~70 seconds to ~15 seconds on Edge devices.
- **Pure Python Gatekeeping:** Hallucination prevention is done via deterministic, pure-Python logic prior to LLM synthesis, preserving critical battery and VRAM on mobile devices.

---

## 2. DB25 Hybrid Search (Zero-Dependency)

HealthExpert utilizes a custom hybrid search algorithm named **DB25** that operates entirely within the embedded `ChromaDB` layer, requiring zero external database servers.

- **Dense Retrieval (Semantic):** Uses on-device embeddings (e.g., `BAAI/bge-m3`) to find meaning-based matches.
- **Sparse Retrieval (BM25):** Uses a local `rank-bm25` implementation for exact keyword matching (crucial for medical codes, acronyms, and legal terminology).
- **Reciprocal Rank Fusion (RRF):** Fuses the results locally in memory to provide the highest accuracy context to the Analyst Agent.

---

## 3. Extreme Hardware Efficiency & Quantization

To run offline, the models must fit into restricted memory bandwidths.

- **4-Bit NF4 Quantization:** On Desktop/Workstation tiers, HealthExpert uses `bitsandbytes` to compress 8B parameter models down to ~5.5GB of VRAM.
- **GGUF & llama.cpp:** On Edge devices (Raspberry Pi, low-end laptops), HealthExpert seamlessly switches to GGUF formatted models, allowing CPU-based inference and offloading specific layers to available NPUs/TPUs.
- **MLC-LLM (Mobile):** For mobile and handheld deployments, models are compiled natively to utilize Apple's Core ML or Android's NNAPI, ensuring the battery is not drained by CPU-bound tensor operations.

---

## 4. Secure Air-Gapped Operation

- **100% Localhost Binding:** Internal microservices bind strictly to `127.0.0.1`.
- **Fernet Encryption:** All ingested documents and generated vector embeddings are encrypted at rest using AES-256. If a handheld device is lost in the field, the raw text cannot be extracted from the ChromaDB SQLite files.
- **No Telemetry:** The system has all OTel (OpenTelemetry) and CrewAI tracking explicitly disabled (`os.environ["CREWAI_TELEMETRY_OPT_OUT"] = "true"`), ensuring absolute silence on the network interface.
