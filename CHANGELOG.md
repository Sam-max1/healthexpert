# Changelog

All notable changes to HealthExpert are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.1] - 2026-05-29

### Changed

- Updated GitHub-facing release artifacts and version references to 1.0.1.
- Refreshed reported issue template guidance to reflect the new release tag.

## [1.0.0] - 2024-05-28

### Added

#### Core Features
- ✨ **Multi-Agent RAG System**: Ingestor, Comprehensive Reader, Gatekeeper, and Analyst agents
- 📄 **Multi-Format Document Support**: PDF, DOCX, XLSX, CSV, TXT, and Image files with OCR
- 🗄️ **Hybrid Retrieval**: Vector DB (Weaviate) + Graph DB (Neo4j) for comprehensive document search
- 🤖 **CrewAI Integration**: Production-ready agent orchestration framework
- 🌐 **Web UI**: Drag-and-drop ingestion, real-time streaming queries, source citations
- ⚡ **Microservice Architecture**: Dedicated LLM generation and embedding servers
- 🔍 **KV-Cache Optimization**: Full-document inference with cached representations

#### API Endpoints
- `POST /api/ingest` - Document ingestion with async job tracking
- `POST /api/query` - Query with Server-Sent Events streaming
- `GET /api/ingest/status/<job_id>` - Monitor ingestion progress
- `GET /api/documents` - List ingested documents
- `GET /api/status` - System health check

#### CLI Tools
- `python healthexpert.py ingest <file>` - CLI document ingestion
- `python healthexpert.py query "<question>"` - CLI queries
- `python healthexpert.py list` - List documents
- `python healthexpert.py clear` - Clear all documents

#### Infrastructure
- 🐳 **Docker Support**: Complete docker-compose setup
- 📝 **Comprehensive Documentation**: Architecture design, API documentation
- ✅ **Unit & Integration Tests**: Test suite coverage
- 🔐 **Security**: Environment-based configuration, input validation

### Technical Details

#### Agent Pipeline

**Ingestion:**
- Document loader with 7 file type support
- Recursive character text splitter (512 tokens, 64 overlap)
- BGE-m3 embedding model (1024-dimensional dense vectors)
- Automatic entity extraction for graph DB

**Query:**
1. Comprehensive Agent: Full-document reasoning (KV cache)
2. Gatekeeper Agent: Context verification (groundedness check)
3. Analyst Agent: Answer synthesis with citations

#### Models

- **LLM**: Qwen/Qwen3-8B (8B parameters)
- **Embeddings**: BAAI/bge-m3 (dense + sparse + ColBERT)
- **Chunking**: RecursiveCharacterTextSplitter (512 tokens)

#### Dependencies

- Flask 3.0+ for web framework
- CrewAI 0.36+ for agent orchestration
- LangChain 0.2+ for LLM integration
- Weaviate 0.5+ for vector storage
- Neo4j 5.18+ for graph database
- Python 3.10+ runtime

### Documentation

- [README.md](README.md) - Quick start and usage guide
- [CONTRIBUTING.md](CONTRIBUTING.md) - Developer guidelines
- [SECURITY.md](SECURITY.md) - Security best practices
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) - Community standards
- [HEALTHEXPERT_ARCHITECTURE_DESIGN.md](HEALTHEXPERT_ARCHITECTURE_DESIGN.md) - System architecture

### Configuration

Environment variables for:
- LLM server configuration (port 8002)
- Embedding server configuration (port 8003)
- Weaviate persistence
- Neo4j database
- Flask application
- RAG parameters (chunk size, overlap, top-k)

### Performance

- Batch embedding (12 documents per batch by default)
- Async ingestion with job tracking
- KV-cache optimization for faster queries
- Fallback retrieval (vector + graph search)
- Configurable timeouts and resource limits

### Known Issues

None at this time. Please report issues to [GitHub Issues](https://github.com/Sam-max1/healthexpert/issues).

### Future Roadmap

- [ ] Advanced metrics dashboard
- [ ] Multi-language support
- [ ] Fine-tuned domain models
- [ ] Enterprise authentication (OAuth2, SAML)
- [ ] Prompt versioning and A/B testing
- [ ] Batch processing API
- [ ] Web UI improvements (dark mode, themes)
- [ ] Mobile app support
- [ ] Vector store optimization (HNSW, Annoy)
- [ ] Custom agent templates

---

## Version History

- **1.1.0** - Added headless API endpoints, HuggingFace Docker support, and updated version tag (May 31, 2026)
- **1.0.1** - Release artifact refresh and version tag update (May 29, 2026)
- **1.0.0** - Initial release (May 28, 2024)

---

For detailed migration guides, please see [UPGRADE.md](UPGRADE.md) (when available).
