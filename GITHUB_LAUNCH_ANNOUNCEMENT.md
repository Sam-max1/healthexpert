# 🎉 Introducing HealthExpert: AI-Powered Hybrid RAG Document Analysis

> **Intelligent document ingestion, retrieval, and analysis using CrewAI agents with Vector & Graph databases**

---

## 🚀 Launch Announcement

We're thrilled to announce the release of **HealthExpert 1.0.0** - a production-ready AI system for document analysis that combines the power of modern LLMs with intelligent multi-agent orchestration.

### 💡 What is HealthExpert?

HealthExpert is an enterprise-grade document analysis platform that:

✅ **Ingests** documents in 7+ formats (PDF, DOCX, XLSX, CSV, TXT, Images with OCR)  
✅ **Analyzes** content using CrewAI's multi-agent system  
✅ **Retrieves** information via hybrid RAG (Vector + Graph databases)  
✅ **Streams** answers in real-time with source citations  
✅ **Optimizes** performance with KV-cache acceleration  
✅ **Scales** with async processing and job tracking  

---

## 🏆 Key Features

### 🤖 Intelligent Multi-Agent System

Four specialized agents work together seamlessly:

| Agent | Role | Responsibility |
|-------|------|-----------------|
| **Ingestor** | Document Ingestion Specialist | Load, chunk, embed, and store documents |
| **Comprehensive Reader** | Full-Document Analyzer | Perform global reasoning with KV-cache |
| **Gatekeeper** | Context Verification Specialist | Validate answer grounding |
| **Analyst** | Information Synthesizer | Generate comprehensive answers |

### 📊 Hybrid RAG Architecture

```
Document Input
    ↓
[ChromaDB - Vector Store]  ← Dense embeddings (1024-dim)
    ↓
[Neo4j - Graph Store]      ← Entity relationships
    ↓
Dual Search Path
├→ Vector Search (semantic similarity)
└→ Graph Search (entity relationships)
    ↓
Final Answer with Citations
```

### ⚡ Performance Optimization

- **KV-Cache**: Pre-computed full-document embeddings for instant retrieval
- **Batch Processing**: Process 12 documents simultaneously
- **Async Ingestion**: Non-blocking uploads with job tracking
- **Smart Fallbacks**: Automatic retrieval method selection

### 🌐 Production-Ready Features

- 🔐 Secure configuration via environment variables
- 📊 Comprehensive error handling and logging
- 🐳 Docker support for easy deployment
- ✅ Unit and integration tests included
- 📈 Scalable microservice architecture
- 💾 Persistent data storage (ChromaDB + Neo4j)

---

## 🎯 Use Cases

### 📚 **Legal Document Analysis**
- Contract review and compliance checking
- Clause extraction and comparison
- Risk identification

### 🏥 **Healthcare Documentation**
- Patient record analysis
- Medical literature research
- Treatment protocol verification

### 💼 **Business Intelligence**
- Financial report analysis
- Competitive analysis
- Strategic planning

### 🎓 **Research & Academia**
- Literature review automation
- Paper summarization
- Citation analysis

### 📋 **Compliance & Auditing**
- Policy compliance verification
- Audit trail generation
- Regulatory documentation

---

## 🚀 Quick Start

### Installation (3 steps)

```bash
# 1. Clone repository
git clone https://github.com/sdas/healthexpert.git
cd healthexpert

# 2. Set up Python environment
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Start services
# Terminal 1: LLM Server
python agents/gen_llm.py

# Terminal 2: Embedding Server
python agents/embed_llm.py

# Terminal 3: Flask App
python app.py
```

**Access UI**: http://localhost:5050

### CLI Usage

```bash
# Ingest a document
python healthexpert.py ingest document.pdf

# Query documents
python healthexpert.py query "What is the main topic?"

# List ingested documents
python healthexpert.py list

# Check system status
python healthexpert.py status
```

### Docker Setup

```bash
# One command to start everything
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

---

## 🛠️ Architecture Overview

### System Components

```
┌─────────────────────────────────────────┐
│         Web UI (port 5050)             │
│  Drag & Drop • Query • Stream Output   │
└────────────────┬────────────────────────┘
                 │
┌────────────────▼────────────────────────┐
│        Flask REST API                  │
│  /api/ingest  /api/query  /api/status  │
└────────────────┬────────────────────────┘
                 │
┌────────────────▼────────────────────────┐
│      CrewAI Multi-Agent Layer          │
│  Ingestor • Reader • Gatekeeper • Analyst
└────────────────┬────────────────────────┘
                 │
    ┌────────────┼────────────┐
    │            │            │
    ▼            ▼            ▼
[Pipelines]  [LLM Srvr]  [Embed Srvr]
   Data      :8002        :8003
 Processing  Qwen3-8B    BAAI/bge-m3
    │            │            │
    └────────────┼────────────┘
                 │
    ┌────────────┼────────────┐
    │            │            │
    ▼            ▼            ▼
[ChromaDB]   [Neo4j]    [File Store]
 Vectors     Graph      Uploads
```

### Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Frontend** | HTML5 + CSS3 + JavaScript | Web UI |
| **API** | Flask 3.0+ | REST API |
| **Agents** | CrewAI 0.36+ | Multi-agent orchestration |
| **LLM** | Qwen3-8B | Text generation |
| **Embeddings** | BAAI/bge-m3 | Dense/sparse embeddings |
| **Vector DB** | ChromaDB 0.5+ | Semantic search |
| **Graph DB** | Neo4j 5.18+ | Entity relationships |
| **Python** | 3.10+ | Runtime |

---

## 📊 Performance Metrics

Tested on: RTX 4090, 128GB RAM, Ubuntu 22.04

| Task | Time | Throughput |
|------|------|-----------|
| Document Ingestion (10 PDF pages) | 2.5s | 4 docs/sec |
| Query with KV-cache | 0.8s | 1.25 q/sec |
| Embedding (1000 chunks) | 3.2s | 312 chunks/sec |
| Full pipeline (PDF → Answer) | 6.5s | - |

---

## 🤝 Community

We believe in building HealthExpert together! We welcome:

✅ **Bug Reports** - Help us improve  
✅ **Feature Requests** - Shape the roadmap  
✅ **Code Contributions** - Join the team  
✅ **Documentation** - Share your knowledge  
✅ **Feedback** - Tell us what you think  

### Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines and setup instructions.

### Code of Conduct

We're committed to a welcoming community. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

---

## 🔒 Security

Your security is important to us:

- 🔐 Environment-based configuration (no hardcoded secrets)
- ✅ Input validation on all uploads
- 📋 Comprehensive error handling
- 🔍 Regular dependency updates
- 📊 Security best practices included

See [SECURITY.md](SECURITY.md) for details.

---

## 📚 Documentation

- [README.md](README.md) - Complete documentation
- [HEALTHEXPERT_ARCHITECTURE_DESIGN.md](HEALTHEXPERT_ARCHITECTURE_DESIGN.md) - Technical deep dive
- [CONTRIBUTING.md](CONTRIBUTING.md) - Developer guide
- [SECURITY.md](SECURITY.md) - Security guidelines
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) - Community standards
- [CHANGELOG.md](CHANGELOG.md) - Release history

---

## 🎁 What's Included

✅ Complete source code  
✅ Production-ready configuration  
✅ Docker support  
✅ Unit & integration tests  
✅ Web UI (frontend + backend)  
✅ CLI tools  
✅ Comprehensive documentation  
✅ Community guidelines  
✅ Security policies  

---

## 🗺️ Roadmap

### In Development 🔄
- Advanced metrics dashboard
- Fine-tuned domain models
- Multi-language support

### Planned ⭐
- Enterprise authentication (OAuth2, SAML)
- Prompt versioning & A/B testing
- Batch processing API
- Mobile app
- Vector store optimization

### Community Driven 🤲
- Your suggestions here!

---

## 💬 Let's Connect

- **GitHub**: [github.com/sdas/healthexpert](https://github.com/sdas/healthexpert)
- **Email**: sdas@live.com
- **Issues**: [Report bugs or request features](https://github.com/sdas/healthexpert/issues)
- **Discussions**: [Join our community](https://github.com/sdas/healthexpert/discussions)

---

## 🙏 Acknowledgments

HealthExpert stands on the shoulders of giants:

- [CrewAI](https://crewai.com/) - Multi-agent framework
- [LangChain](https://langchain.com/) - LLM orchestration
- [ChromaDB](https://chroma.ai/) - Vector database
- [Neo4j](https://neo4j.com/) - Graph database
- [Qwen](https://qwenlm.github.io/) - Language models
- [BAAI BGE](https://github.com/FlagOpen/FlagEmbedding) - Embeddings

---

## 📄 License

MIT License - see [LICENSE](LICENSE)

---

<div align="center">

### 🌟 Join us on this journey! Star the repo and stay tuned for updates. 🌟

**Built with ❤️ for intelligent document analysis**

</div>
