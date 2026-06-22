---
title: HealthExpert
emoji: 🏥
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# HealthExpert 🏥
**Version 1.0 Public Release**

![HealthExpert Admin UI Dashboard](static/screenshot.png)<div align="center">

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.0%2B-green?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![CrewAI](https://img.shields.io/badge/CrewAI-0.36%2B-orange?logo=robot&logoColor=white)](https://crewai.com/)
[![License](https://img.shields.io/badge/License-MIT-purple)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Production%20Ready-brightgreen)](https://github.com/Sam-max1/healthexpert)

**AI-Powered Hybrid RAG Document Analysis System**

*Intelligent document ingestion, retrieval, and analysis using CrewAI agents with Vector & Graph databases*

[🚀 Quick Start](#quick-start) • [📚 Documentation](#documentation) • [🏗️ Architecture](#architecture) • [🤝 Contributing](#contributing)

</div>

---

## 🌟 Overview

**HealthExpert** is an enterprise-grade AI document analysis platform combining:

- **🤖 Multi-Agent RAG**: Specialized pipeline spanning Hybrid DBs (Vector + BM25 + Graph)
- **📱 Nitdaa HF Sibling**: A mobile-friendly MVP UI deployed on HuggingFace Spaces with multi-tenant session isolation. [Launch Application (sam-max1-nitdaa.hf.space)](https://sam-max1-nitdaa.hf.space/)
- **⚡ High-Speed Ingestion**: Fast local spaCy NER pipeline for automated Kuzu graph construction (no LLM required)
- **🧠 Reasoning-Distilled Local Models**: Fast edge execution using Llama.cpp and Qwen/Phi tiny models
- **📥 Dynamic KB Sync**: Auto-syncs private knowledge bases from HuggingFace Datasets on boot
- **🌐 Web UI**: Real-time streaming responses with detailed metric telemetry
- **🔐 Production-Ready**: Token-based session tracking, JSON auditing, error handling, and Docker support

### Key Features

| Feature | Description |
|---------|-------------|
| **Hybrid Retrieval** | KV-cache optimization, Vector + Graph + BM25 search fallback |
| **Mobile Sibling** | Nitdaa Edge UI with responsive design & HuggingFace deployment |
| **Document Support** | 7 file types with automatic format detection |
| **Real-time Streaming** | SSE-based streaming responses with real-time timers and token telemetry |
| **Async Processing** | Non-blocking document ingestion with job tracking & HF Dataset sync |
| **Session Auditing** | `nitdaa_sessions.json` and `nitdaa_summary.json` structured JSON tracking |
| **Admin Dashboard** | Monitor system status, manage documents, view embeddings |
| **Docker Ready** | Complete docker-compose setup included |

---

## 🏗️ Architecture

### System Design

```
┌─────────────────────────────────────────────────────────────┐
│                    Flask Web UI (port 5050)                │
│         Document Ingestion • Query • Output Rendering      │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│               Flask REST API (app.py)                       │
│  POST /api/ingest  │  POST /api/query  │  GET /api/status  │
└────────────────────┬────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────┐
│            CrewAI Agent Layer (agents/crew.py)             │
│  • Ingestor Agent      → Document loading & chunking       │
│  • Comprehensive Agent → Full-document reasoning (KV cache)│
│  • Gatekeeper Agent    → Context verification              │
│  • Analyst Agent       → Answer synthesis                   │
└────────────────────┬────────────────────────────────────────┘
                     │
     ┌───────────────┼───────────────┐
     │               │               │
┌────▼────┐ ┌───────▼────┐ ┌────────▼──────┐
│ Pipeline│ │  LLM Srvr  │ │ Embed Server │
│  Data   │ │ :8002      │ │    :8003     │
│Processing│ │Qwen2.5-1.5B-Instruct    │ │ BAAI/bge-small-en-v1.5  │
└────┬────┘ └───────────┘ └──────────────┘
     │
     ├─→ ChromaDB (Vector Store, embedded, BM25 hybrid search)
     └─→ Kuzu (Graph DB)
```

### Data Flow: Ingestion Pipeline

```
User Upload
    ↓
[Document Loader] → Extract text (PDF, DOCX, XLSX, CSV, TXT, OCR)
    ↓
[Chunker] → Split into 512-token chunks (64 overlap)
    ↓
[Embedder] → Generate dense/sparse embeddings (BAAI/bge-small-en-v1.5)
    ↓
┌─────────────────────────────────────────┐
│ [ChromaDB] Vector Store (embedded)      │
│ Stores: chunks + embeddings + metadata  │
│ Search: Dense ANN + BM25                │
└─────────────────────────────────────────┘
    ↓
[Entity Extraction] → spaCy Local NER (fast, 0 LLM cost)
    ↓
┌─────────────────────────────────────────┐
│ [Kuzu] Graph DB                        │
│ Stores: entities + relationships        │
└─────────────────────────────────────────┘
```

### Data Flow: Query Pipeline

```
User Query
    ↓
[Comprehensive Agent] → Full-document reasoning (KV cache)
    ↓
[Context Verification] → Gatekeeper validates groundedness
    ↓
[Answer Synthesis] → Analyst generates markdown response
    ↓
[SSE Streaming] → Real-time chunks to UI
    ↓
User sees answer with source citations
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- Docker & Docker Compose (optional)
- 8GB+ RAM recommended
- CUDA/ROCm support (optional, for GPU acceleration)

### Installation

#### 1. Clone Repository

```bash
git clone https://github.com/Sam-max1/healthexpert.git
cd healthexpert
```

#### 2. Set Up Python Environment

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set HuggingFace token for private KB document syncing
export HF_PRIVATE_TOKEN=$(secret-tool lookup api huggingface)
```

#### 3. Start Microservices

**Terminal 1 - LLM Generation Server (port 8002):**
```bash
python agents/gen_llm.py
# Expected output:
# * Running on http://127.0.0.1:8002
```

**Terminal 2 - Embedding Server (port 8003):**
```bash
python agents/embed_llm.py
# Expected output:
# * Running on http://127.0.0.1:8003
```

**Terminal 3 - Main Flask App (port 5050):**
```bash
python app.py
# Expected output:
# * Running on http://127.0.0.1:5050
```

#### 4. Access Web UI

Open your browser: **http://localhost:5050**

### Using Docker Compose

```bash
# Start all services
# Ensure HF_PRIVATE_TOKEN is set in your environment or .env file before running
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### CLI Usage

```bash
# Ingest a document
python healthexpert.py ingest path/to/document.pdf

# Query documents
python healthexpert.py query "What is the main topic?"

# List ingested documents
python healthexpert.py list

# Check system status
python healthexpert.py status

# Clear all documents
python healthexpert.py clear
```

---

## 📚 Documentation

### Project Structure

```
healthexpert/
├── app.py                                    # Flask REST API
├── config.py                                 # Configuration (env-based)
├── healthexpert.py                           # CLI interface
├── requirements.txt                          # Python dependencies
├── docker-compose.yml                        # Docker setup
│
├── agents/                                   # CrewAI agents
│   ├── crew.py                              # Crew orchestration
│   ├── llm.py                               # LLM integration
│   ├── tools.py                             # Agent tools
│   ├── gen_llm.py                           # LLM generation server (port 8002)
│   └── embed_llm.py                         # Embedding server (port 8003)
│
├── pipeline/                                # Data processing
│   ├── document_loader.py                   # Multi-format document loader
│   ├── chunker.py                           # Text chunking (512 tokens)
│   ├── embedder.py                          # Embedding HTTP client
│   ├── vector_store.py                      # ChromaDB + BM25 hybrid search
│   └── graph_store.py                       # Kuzu integration
│
├── templates/                               # Web UI (HTML)
│   └── index.html                           # Main interface
│
├── static/                                  # Frontend assets
│   ├── app.js                               # WebSocket + SSE handling
│   └── style.css                            # UI styling
│
└── data/                                    # Runtime data
    ├── security.key                         # Fernet key (local-only)
    └── uploads/                             # Uploaded documents
```

### Environment Configuration

Create `.env` file to override defaults:

```env
# LLM Generation Server (port 8002)
LLM_BASE_URL=http://127.0.0.1:8002
HF_PRIVATE_TOKEN=your_huggingface_token_here
LLM_MODEL_ID=Qwen/Qwen2.5-1.5B-Instruct
LLM_MAX_TOKENS=2048
LLM_TEMPERATURE=0.7
LLM_TOP_P=0.9
LLM_TIMEOUT=600

# Embedding Server (port 8003)
EMBED_BASE_URL=http://127.0.0.1:8003
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
EMBEDDING_BATCH_SIZE=12
EMBEDDING_TIMEOUT=120

# Vector Database (ChromaDB — embedded, no server required)
CHROMA_PERSIST_DIR=./data/chroma_db
CHROMA_COLLECTION=Document
ENCRYPTION_KEY_FILE=./data/security.key

# Kuzu
KUZU_URI=bolt://localhost:7687
KUZU_USER=kuzu
KUZU_PASSWORD=healthexpert

# Flask
UPLOAD_FOLDER=./uploads
SECRET_KEY=your-secret-key-here
CHUNK_SIZE=512
CHUNK_OVERLAP=64
```

### API Endpoints

#### Ingestion

**POST /api/ingest**
```bash
curl -X POST -F "file=@document.pdf" http://localhost:5050/api/ingest

# Response:
# { "job_id": "abc-123", "status": "processing" }
```

#### Query

**POST /api/query**
```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"query":"What is the main topic?"}' \
  http://localhost:5050/api/query

# Returns: Server-Sent Events stream
```

#### Status

**GET /api/ingest/status/<job_id>**
```bash
curl http://localhost:5050/api/ingest/status/abc-123
```

---

## 🔧 Development

### Running Tests

```bash
# Run integration tests
python -m pytest HEALTHEXPERT_UNIT_INTEGRATION_TEST.md -v

# Run specific agent test
python -m pytest agents/test_agents.py -v
```

### Code Style

```bash
# Format code
black healthexpert/ agents/ pipeline/

# Lint
flake8 healthexpert/ agents/ pipeline/ --max-line-length=100
```

### Debugging

Enable debug logging:

```bash
export LOG_LEVEL=DEBUG
python app.py
```

View logs:

```bash
tail -f logs/app.log
```

---

## 🤝 Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### How to Contribute

1. **Fork** the repository
2. **Create** a feature branch (`git checkout -b feature/amazing-feature`)
3. **Commit** changes (`git commit -m 'Add amazing feature'`)
4. **Push** to branch (`git push origin feature/amazing-feature`)
5. **Open** a Pull Request

### Development Setup

```bash
# Clone fork
git clone https://github.com/YOUR_USERNAME/healthexpert.git

# Create development environment
python -m venv venv_dev
source venv_dev/bin/activate
pip install -r requirements.txt

# Install dev tools
pip install pytest black flake8

# Run tests
pytest tests/
```

---

## 📋 Roadmap

- [x] Multi-agent RAG pipeline
- [x] Web UI with streaming responses
- [x] Docker containerization
- [x] Hybrid vector+graph retrieval
- [ ] Advanced metrics dashboard
- [ ] Multi-language support
- [ ] Fine-tuned domain models
- [ ] Enterprise auth (OAuth2, SAML)
- [ ] Prompt versioning
- [ ] Batch processing API

---

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 👨‍💻 Author

**Sam-max1**

<div align="center">

### 🌟 If you find this project helpful, please consider giving it a star! ⭐

</div>

---

## 🙏 Acknowledgments

- [CrewAI](https://crewai.com/) - Multi-agent framework
- [LangChain](https://langchain.com/) - LLM orchestration
- [ChromaDB](https://www.trychroma.com/) - Embedded vector database
- [rank-bm25](https://github.com/dorianbrown/rank_bm25) - BM25 for sparse search
- [Kuzu](https://kuzu.com/) - Graph database
- [Qwen](https://qwenlm.github.io/) / [Phi-3.5] - Local Edge LLMs
- [spaCy](https://spacy.io/) - Local Entity Extraction
- [BAAI BGE](https://github.com/FlagOpen/FlagEmbedding) - Embedding models

---

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/Sam-max1/healthexpert/issues)
- **LinkedIn DM**: [Sam-max1](https://www.linkedin.com/in/sam-max1)
- **Documentation**: See [HEALTHEXPERT_ARCHITECTURE_DESIGN.md](HEALTHEXPERT_ARCHITECTURE_DESIGN.md)

---

<div align="center">

**Built with ❤️ for AI-powered document analysis**

</div>
