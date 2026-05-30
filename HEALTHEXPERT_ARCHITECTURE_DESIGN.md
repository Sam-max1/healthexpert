# Document AI Expert — Architecture Design

> AI-powered document analysis using hybrid RAG (Vector DB + Graph DB) with CrewAI agents.

---

## System Overview

```mermaid
graph TB
    subgraph UI["Flask Web UI (port 5050)"]
        A[Document Ingestion Panel\nDrag & Drop · PDF/DOCX/XLSX/CSV/TXT/IMG]
        B[Query / Chat Panel\nNatural language input · SSE streaming]
        C[Output Panel\nMarkdown rendering · Source citations]
    end

    subgraph Backend["Flask REST API (app.py)"]
        D[POST /api/ingest]
        E[POST /api/query]
        F[GET  /api/documents]
        G[GET  /api/status]
    end

    subgraph Agents["CrewAI Agent Layer"]
        H[Ingestor Agent\nRole: Document Ingestion Specialist]
        I[Comprehensive Agent\nRole: Comprehensive Document Reader]
        J[Gatekeeper Agent\nRole: Context Verification Specialist]
        K[Analyst Agent\nRole: Information Analyst]
    end

    subgraph Pipeline["Data Pipeline"]
        L[document_loader.py\ntxt · pdf · docx · xlsx · csv · image OCR]
        M[chunker.py\nRecursiveCharacterTextSplitter\n512 tokens · 64 overlap]
        N[embedder.py\nHTTP client → port 8003]
    end

    subgraph Stores["Knowledge Stores"]
        O[(ChromaDB\nVector Store\nEmbedded · DB25 Hybrid Search\nTier: Foundation / Extended)]
        P[(Neo4j\nGraph DB\nTier: Foundation / Extended)]
    end

    subgraph GenLLM["Gen LLM Server — agents/gen_llm.py (port 8002)"]
        Q[transformers AutoModelForCausalLM\nQwen/Qwen3-8B · 4-bit NF4\nPOST /v1/completions\nPOST /v1/kv_cache]
    end

    subgraph EmbedLLM["Embed LLM Server — agents/embed_llm.py (port 8003)"]
        R[FlagEmbedding.BGEM3FlagModel\nBAAI/bge-m3\nPOST /v1/embeddings\nPOST /v1/embeddings/multi]
    end

    A -->|upload files| D
    B -->|query| E
    D --> H
    E --> I --> J --> K
    H --> L --> M --> N --> O
    H -->|entity extraction| Q --> P
    I -->|use_kv_cache| Q
    K -->|synthesis| Q
    K --> C
    F --> O
    G --> O & P & Q & R
```

---

## Data Flow

### Ingestion Path

```mermaid
sequenceDiagram
    participant U as User (Browser)
    participant F as Flask API
    participant IA as Ingestor Agent
    participant DL as Document Loader
    participant CH as Chunker
    participant EM as embedder.py (HTTP client)
    participant ES as embed_llm.py :8003
    participant VDB as ChromaDB (embedded)
    participant GS as gen_llm.py :8002
    participant GDB as Neo4j

    U->>F: POST /api/ingest (file upload)
    F->>F: Save to /uploads, create job
    F-->>U: { job_id }
    F->>IA: run_ingest_crew(file_path)
    IA->>DL: load_document(file_path)
    DL-->>IA: pages [text, metadata]
    IA->>CH: chunk_documents(pages)
    CH-->>IA: chunks [text, metadata, chunk_index]
    IA->>EM: embed_texts(chunks)
    EM->>ES: POST /v1/embeddings {input: chunks}
    ES-->>EM: {data: [{embedding: [...]}]}
    EM-->>IA: dense vectors [1024-dim]
    IA->>VDB: add_chunks(chunks, embeddings, doc_id)
    VDB-->>IA: stored count
    IA->>GS: POST /v1/completions {prompt: entity_extraction_prompt}
    GS-->>IA: JSON entity array
    IA->>IA: Parse JSON (robust error handling)
    IA->>GDB: store_entities(entities, source)
    U->>F: GET /api/ingest/status/{job_id}
    F-->>U: { status, results }
```

### Query / Retrieval Path

```mermaid
sequenceDiagram
    participant U as User (Browser)
    participant F as Flask API (SSE)
    participant CA as Comprehensive Agent
    participant GA as Gatekeeper Agent
    participant AA as Analyst Agent
    participant GS as gen_llm.py :8002

    U->>F: POST /api/query { query }
    F-->>U: SSE stream open
    F->>CA: run_query_crew(query)
    
    %% Phase 1
    CA->>GS: POST /v1/completions {use_kv_cache: true}
    alt KV Cache Success
        GS-->>CA: Precompiled Full-Document Inference
    else KV Cache Failure
        CA->>VDB: Fallback DB25 hybrid search (Dense + BM25)
        CA->>GDB: Fallback standard graph search
    end
    
    %% Phase 2
    CA->>GA: Context Evaluation (ContextVerificationSpecialist)
    GA->>GS: POST /v1/completions
    GS-->>GA: YES or NO (Is grounded?)
    
    %% Phase 3
    GA->>AA: Synthesize Answer (if YES)
    AA->>GS: POST /v1/completions
    GS-->>AA: Markdown answer
    AA-->>F: answer string
    F-->>U: SSE chunks → Markdown rendered
```

---

## Microservices

### `agents/gen_llm.py` — LLM Generation Server (port 8002)

| Item | Value |
|---|---|
| **Port** | `8002` |
| **Model** | `Qwen/Qwen3-8B` |
| **Quantization** | 4-bit NF4 (bitsandbytes) — ~5.5 GB VRAM |
| **Backend** | `transformers` AutoModelForCausalLM (`device_map="auto"`) |
| **Start** | `python agents/gen_llm.py` |
| **Endpoints** | `GET /health`, `POST /v1/completions`, `POST /v1/kv_cache` |

**Request — `POST /v1/completions`:**
```json
{
  "prompt":       "The capital of France is",
  "max_tokens":   2048,
  "temperature":  0.7,
  "top_p":        0.9,
  "thinking_mode": false
}
```
**Response:**
```json
{
  "model": "Qwen/Qwen3-8B",
  "choices": [{ "index": 0, "text": " Paris.", "thinking": "" }]
}
```

**Health Response:**
```json
{
  "status": "ok",
  "model": "Qwen/Qwen3-8B",
  "quantization": "4-bit NF4 (bitsandbytes)",
  "gpu_id": "cuda:0",
  "kv_cache_length": 1024
}
```

---

### `agents/embed_llm.py` — Embedding Server (port 8003)

| Item | Value |
|---|---|
| **Port** | `8003` |
| **Model** | `BAAI/bge-m3` |
| **Backend** | `FlagEmbedding.BGEM3FlagModel` (fp16) |
| **Start** | `python agents/embed_llm.py` |
| **Endpoints** | `GET /health`, `POST /v1/embeddings`, `POST /v1/embeddings/multi` |

**Request — `POST /v1/embeddings`** (OpenAI-compatible, dense only):
```json
{ "input": "What is covered under plan X?" }
```
**Response:**
```json
{
  "object": "list",
  "model":  "BAAI/bge-m3",
  "data":   [{ "object": "embedding", "index": 0, "embedding": [0.021, -0.043, ...] }]
}
```

**Request — `POST /v1/embeddings/multi`** (dense + sparse + ColBERT):
```json
{
  "sentences_1": ["What is BGE M3?"],
  "sentences_2": ["BGE M3 is an embedding model supporting dense retrieval..."],
  "weights":     [0.4, 0.2, 0.4]
}
```
**Response includes:** `dense_similarity`, `lexical_weights_1/2`, `colbert_scores`, `combined_scores`

---

## Component Descriptions

| Component | File | Responsibility |
|---|---|---|
| **Gen LLM Server** | `agents/gen_llm.py` | Flask :8002 — Qwen/Qwen3-8B 4-bit NF4, `/v1/completions`, `/v1/kv_cache`, `/health` |
| **Embed LLM Server** | `agents/embed_llm.py` | Flask :8003 — BGEM3 dense/sparse/ColBERT, `/v1/embeddings`, `/v1/embeddings/multi`, `/health` |
| Config | `config.py` | All settings; LLM_BASE_URL (8002) + EMBED_BASE_URL (8003); ChromaDB path; override via env vars |
| Local LLM | `agents/llm.py` | LangChain wrapper → `POST /v1/completions` on port 8002 |
| Embedder | `pipeline/embedder.py` | HTTP client → `POST /v1/embeddings` on port 8003 |
| Tools | `agents/tools.py` | 5 CrewAI `@tool` functions |
| Crew | `agents/crew.py` | Phase 1 (KV Cache), Phase 2 (Gatekeeper), Phase 3 (Analyst) |
| Document Loader | `pipeline/document_loader.py` | Parse 9 file formats; OCR for images |
| Chunker | `pipeline/chunker.py` | Recursive splitting (langchain_text_splitters), flat chunk dicts |
| Vector Store | `pipeline/vector_store.py` | **ChromaDB** CRUD, **DB25 hybrid search** (Dense + BM25 via RRF), tier support |
| Graph Store | `pipeline/graph_store.py` | Neo4j CRUD, OPTIONAL MATCH, tier support |
| Flask App | `app.py` | REST API + Docker control + Tier Access Admin Auth + KV trigger + Health Metrics |
| CLI | `healthexpert.py` | Standalone command-line interface |
| UI Template | `templates/index.html` | Three-panel SPA + Docker buttons + preset prompts + diagnostic log |
| Styles | `static/style.css` | Dark glassmorphism + Docker modal + log box + preset btn styles |
| JavaScript | `static/app.js` | Fetch/SSE + diag logger + Docker control + probe handlers |
| Test Doc | `HEALTHEXPERT_UNIT_INTEGRATION_TEST.md` | 12-suite test spec with runnable commands |

---

## Technology Stack

| Layer | Technology | Version | Reason |
|---|---|---|---|
| Web Framework | Flask | 3.0+ | Lightweight, SSE support |
| Agent Orchestration | CrewAI | 0.36+ | Multi-agent pipelines |
| LLM Chaining | LangChain | 0.2+ | LLM abstraction, text splitting |
| LLM Backend | transformers | 4.57+ | Standard HuggingFace inference, `device_map="auto"` |
| LLM Model | Qwen/Qwen3-8B | — | 8B params, 131K context, dual thinking/standard mode |
| LLM Quantization | bitsandbytes | 0.43+ | 4-bit NF4, nested quant — ~5.5 GB VRAM |
| Embedding Backend | FlagEmbedding | — | BGEM3FlagModel, fp16 |
| Embedding Model | BAAI/bge-m3 | — | 1024-dim dense + sparse + ColBERT |
| Vector Database | **ChromaDB** | 0.5+ | Embedded persistent store — no server required |
| Hybrid Search | **rank-bm25** | 0.2+ | BM25 scorer for DB25 (Dense + BM25) keyword fusion |
| Graph Database | Neo4j Community | 5.18 | APOC plugins, multi-hop traversal |
| PDF | PyMuPDF (fitz) | 1.24+ | Fast, accurate text extraction |
| DOCX | python-docx | 1.1+ | Paragraph-level extraction |
| XLSX/CSV | pandas + openpyxl | 2.0+ | Sheet-aware chunking |
| Image OCR | pytesseract + Pillow | — | Local OCR, no cloud |
| Markdown | marked.js | CDN | Client-side rendering |

---

## DB25 Hybrid Search

**DB25** = **D**ense vector search + **B**M**25** keyword scoring, fused via **Reciprocal Rank Fusion (RRF)**.

Implemented entirely in `pipeline/vector_store.py` — no external service required.

### How It Works

```
query(query_embedding, keyword="coverage limit")
  │
  ├─ Step 1: Dense ANN (ChromaDB cosine)
  │   Fetch top_k × 4 candidates by vector similarity
  │
  ├─ Step 2: BM25 scoring (rank_bm25)
  │   Score same candidate pool using tokenised keyword query
  │
  └─ Step 3: RRF Fusion
      score = 1/(60 + dense_rank) + 1/(60 + bm25_rank)
      Sort descending → return top_k
```

### Advantages

| Property | Pure Dense | Pure BM25 | DB25 (Hybrid) |
|---|---|---|---|
| Semantic similarity | ✅ Strong | ❌ None | ✅ Strong |
| Exact keyword match | ❌ Weak | ✅ Strong | ✅ Strong |
| Handles typos / paraphrase | ✅ | ❌ | ✅ |
| Recall | Moderate | Moderate | **High** |
| Precision for precise terms | Low | High | **High** |

When `keyword=None` (e.g., internal KV cache calls), the system falls back to pure dense search.

---

## LLM Selection Criteria

### 🔍 Embedding Model — BAAI/bge-m3
**Best for: Complex Hybrid Search (Dense + Sparse + ColBERT)**

| Property | Detail |
|---|---|
| **Parameters** | ~567M |
| **VRAM (fp16)** | ~2 GB |
| **Context Window** | 8,192 tokens |
| **Embedding Dim** | 1,024 (dense) |
| **Vector Types** | Dense · Sparse (lexical) · Multi-vector (ColBERT) |
| **Languages** | 100+ (multilingual) |

**Why "M3"?** The name stands for three core capabilities:

- 🌐 **Multi-lingual** — natively handles 100+ languages without separate language models
- 📐 **Multi-granularity** — works at sentence, paragraph, and document level
- 🎯 **Multi-task** — generates all three vector types in a single forward pass:
  - **Dense vectors** — semantic similarity (standard RAG)
  - **Sparse vectors** — keyword-based weights (BM25-style lexical matching)
  - **ColBERT vectors** — token-level interaction for fine-grained re-ranking

---

### 🧠 Generation Model — Qwen3-8B (4-bit NF4 Quantization)
**The 2026 Powerhouse: Best Sub-10B Reasoning Model**

| Property | Detail |
|---|---|
| **Parameters** | ~8.2B |
| **VRAM (float16)** | ~16 GB |
| **VRAM (4-bit NF4)** | ~5.5 GB ✅ |
| **Context Window** | 131,072 tokens (131K) |
| **Quantization** | `BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=bfloat16, bnb_4bit_use_double_quant=True)` |
| **Architecture** | Dual-mode: Thinking + Standard |
| **Inference Backend** | HuggingFace `transformers` + bitsandbytes |

**Why 4-bit NF4?**
- **NF4 (NormalFloat 4-bit)**: Optimal quantization type for normally-distributed weights — superior quality vs INT4.
- **Double quantization**: Quantizes the quantization constants themselves, saving ~0.4 GB additional VRAM.
- **bfloat16 compute**: Maintains numerical stability during forward passes despite 4-bit storage.
- **Net result**: ~3× VRAM reduction (16 GB → 5.5 GB) with minimal quality degradation.

---

### Selection Summary

| Criterion | BAAI/bge-m3 (port 8003) | Qwen3-8B NF4 (port 8002) |
|---|---|---|
| **Role** | Embedding / Retrieval | Text Generation / Synthesis |
| **VRAM** | ~2 GB | ~5.5 GB (4-bit NF4) |
| **Strength** | Hybrid search (dense + sparse + ColBERT) | Complex reasoning + long context |
| **Context** | 8,192 tokens | 131,072 tokens |
| **Key feature** | Three vector types in one pass | Dual thinking/standard mode |
| **Used by** | `pipeline/embedder.py` via HTTP | `agents/llm.py` via HTTP |

---

## Agent Roles

### 🗂️ Ingestor Agent
- **Role**: Document Ingestion Specialist
- **Tools**: `IngestDocumentTool`, `ExtractAndStoreEntitiesTool`
- **Process**: Load → Chunk → Embed (via port 8003) → Store (**ChromaDB**) → Extract entities (via port 8002) → Store graph (Neo4j)

### 🔍 Retriever Agent
- **Status**: Replaced by KV Cache Inference fallback logic.
- **Process**: Directly calls **DB25 vector search** and graph search functions when KV Cache fails.

### 📊 Analyst Agent
- **Role**: Information Analyst
- **Tools**: `SynthesizeAnswerTool`
- **Process**: Receive merged context → prompt LLM (via port 8002) → return structured Markdown with citations (or zero-retrieval guardrail message if no context matches).

---

## Deployment

### Quick Start (Local)

```bash
# ── One-time setup ─────────────────────────────────────────────────────────────

# Install Docker Compose V2 plugin (for Neo4j only)
mkdir -p ~/.docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
     -o ~/.docker/cli-plugins/docker-compose
chmod +x ~/.docker/cli-plugins/docker-compose
docker compose version          # → Docker Compose version v5.x.x

# Add your user to the docker group
sudo usermod -aG docker $USER
newgrp docker                   # activate in the current shell immediately

# ── Per-session startup ────────────────────────────────────────────────────────

# 1. Start Neo4j graph database (ChromaDB is embedded — no docker needed for vector DB)
cd /source/python/code/healthexpert
docker compose up -d

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. (Optional) Install tesseract for OCR
sudo apt-get install -y tesseract-ocr

# 4. Start the Gen LLM server (port 8002)  — in a dedicated terminal
python agents/gen_llm.py
# → https://127.0.0.1:8002/v1/completions  (Qwen/Qwen3-8B, 4-bit NF4)

# 5. Start the Embed LLM server (port 8003) — in a dedicated terminal
python agents/embed_llm.py
# → https://127.0.0.1:8003/v1/embeddings

# 6. Start the Flask web app (port 5050)   — in a dedicated terminal
python app.py
# → https://127.0.0.1:5050
# ChromaDB will auto-create data/chroma_db/ on first ingest

# 7. Or use CLI (requires both servers running)
python healthexpert.py ingest policy.pdf
python healthexpert.py query "What is covered under plan X?"
python healthexpert.py list
python healthexpert.py status
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LLM_BASE_URL` | `https://127.0.0.1:8002` | Gen LLM server URL (gen_llm.py) |
| `LLM_MODEL_ID` | `Qwen/Qwen3-8B` | Model identifier |
| `GEN_HOST` | `127.0.0.1` | gen_llm.py bind host |
| `GEN_PORT` | `8002` | gen_llm.py bind port |
| `EMBED_BASE_URL` | `https://127.0.0.1:8003` | Embed LLM server URL (embed_llm.py) |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | Embedding model identifier |
| `EMBED_HOST` | `127.0.0.1` | embed_llm.py bind host |
| `EMBED_PORT` | `8003` | embed_llm.py bind port |
| `EMBED_FP16` | `true` | Use FP16 for embedding inference |
| `EMBED_MAX_LENGTH` | `8192` | Max token length for bge-m3 |
| `CHROMA_PERSIST_DIR` | `data/chroma_db` | ChromaDB persistent storage directory |
| `CHROMA_COLLECTION` | `Document` | ChromaDB collection name |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j bolt URI |
| `NEO4J_PASSWORD` | `healthexpert` | Neo4j password |
| `CHUNK_SIZE` | `512` | Characters per chunk |
| `TOP_K_VECTOR` | `5` | Vector search top-K (DB25 returns top-K after fusion) |

---

## Admin Controls (Localhost Only)

The application provides a suite of powerful administrative capabilities accessible only when the UI is loaded from `localhost` (127.0.0.1). These controls appear in the top-right navigation bar.

1. **DB Up & DB Down**: Control the lifecycle of the **Neo4j** container directly from the UI via internal `docker compose` orchestration. ChromaDB is embedded and requires no lifecycle management.
2. **Purge DB**: A destructive action that deletes the ChromaDB collection and executes `MATCH (n) DETACH DELETE n` in Neo4j, wiping all ingested data instantly.
3. **Kill Switch**: An emergency abort mechanism. Issues `docker compose down` and forces a hard exit of the Flask server (`os._exit(0)`). Designed to halt runaway CrewAI inference loops.

---

## Directory Structure

```
healthexpert/
├── app.py                    Flask application (port 5050)
├── healthexpert.py           Standalone CLI
├── config.py                 Central configuration (both endpoints + ChromaDB path)
├── docker-compose.yml        Neo4j local instance (ChromaDB needs no container)
├── requirements.txt          Python dependencies
│
├── agents/
│   ├── gen_llm.py            ★ LLM Generation Server (port 8002)
│   │                             transformers · Qwen/Qwen3-8B · 4-bit NF4
│   │                             POST /v1/completions · POST /v1/kv_cache
│   ├── embed_llm.py          ★ Embedding Server (port 8003)
│   │                             FlagEmbedding BGEM3 · BAAI/bge-m3
│   │                             POST /v1/embeddings · POST /v1/embeddings/multi
│   ├── llm.py                LangChain wrapper → port 8002
│   ├── tools.py              CrewAI @tool functions
│   └── crew.py               Crew assembly & execution
│
├── pipeline/
│   ├── document_loader.py    Multi-format document parser
│   ├── chunker.py            Text splitting
│   ├── embedder.py           HTTP client → port 8003
│   ├── vector_store.py       ★ ChromaDB wrapper + DB25 hybrid search
│   └── graph_store.py        Neo4j wrapper
│
├── templates/
│   └── index.html            Three-panel SPA
│
├── static/
│   ├── style.css             Dark glassmorphism design
│   └── app.js                Frontend logic (SSE, Markdown)
│
├── uploads/                  Temporary file storage
└── data/
    ├── security.key          Fernet encryption key
    └── chroma_db/            ★ ChromaDB persistent vector store
```
