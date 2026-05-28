"""Document AI Expert — Central Configuration.

Two backend microservices:
  gen_llm.py   → port 8002  POST /v1/completions  (text generation)
  embed_llm.py → port 8003  POST /v1/embeddings   (dense/sparse/ColBERT)
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

# ── LLM generation server (gen_llm.py — port 8002) ──────────────────────────
LLM_BASE_URL          = os.getenv("LLM_BASE_URL",   "http://127.0.0.1:8002")
LLM_COMPLETIONS_URL   = f"{LLM_BASE_URL}/v1/completions"
LLM_MODEL_ID          = os.getenv("LLM_MODEL_ID",   "Qwen/Qwen3-8B")  # HuggingFace transformers
LLM_MAX_TOKENS        = int(os.getenv("LLM_MAX_TOKENS",   "2048"))
LLM_TEMPERATURE       = float(os.getenv("LLM_TEMPERATURE", "0.7"))
LLM_TOP_P             = float(os.getenv("LLM_TOP_P",       "0.9"))
LLM_TIMEOUT           = int(os.getenv("LLM_TIMEOUT",      "300"))

# ── Embedding server (embed_llm.py — port 8003) ───────────────────────────────
EMBED_BASE_URL        = os.getenv("EMBED_BASE_URL",  "http://127.0.0.1:8003")
EMBED_EMBEDDINGS_URL  = f"{EMBED_BASE_URL}/v1/embeddings"
EMBEDDING_MODEL       = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
EMBEDDING_BATCH_SIZE  = int(os.getenv("EMBEDDING_BATCH_SIZE", "12"))
EMBEDDING_TIMEOUT     = int(os.getenv("EMBEDDING_TIMEOUT",   "120"))

# ── ChromaDB ─────────────────────────────────────────────────────────────────
CHROMA_PERSIST_DIR    = os.getenv("CHROMA_PERSIST_DIR",
                                   str(BASE_DIR / "data" / "chroma_db"))
CHROMA_COLLECTION     = os.getenv("CHROMA_COLLECTION", "documents")

# ── Neo4j ────────────────────────────────────────────────────────────────────
NEO4J_URI             = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER            = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD        = os.getenv("NEO4J_PASSWORD", "healthexpert")
NEO4J_AVAILABLE       = True  # Updated at runtime by graph_store.py

# ── Flask / Upload ────────────────────────────────────────────────────────────
UPLOAD_FOLDER         = os.getenv("UPLOAD_FOLDER", str(BASE_DIR / "uploads"))
MAX_CONTENT_LENGTH    = 50 * 1024 * 1024  # 50 MB
ALLOWED_EXTENSIONS    = {".txt", ".pdf", ".docx", ".xlsx", ".csv",
                          ".png", ".jpg", ".jpeg", ".webp"}
SECRET_KEY            = os.getenv("SECRET_KEY", "healthexpert-dev-key-change-in-prod")

# ── RAG ───────────────────────────────────────────────────────────────────────
CHUNK_SIZE            = int(os.getenv("CHUNK_SIZE", "512"))
CHUNK_OVERLAP         = int(os.getenv("CHUNK_OVERLAP", "64"))
TOP_K_VECTOR          = int(os.getenv("TOP_K_VECTOR", "5"))
TOP_K_GRAPH           = int(os.getenv("TOP_K_GRAPH", "3"))

# ── HuggingFace cache (reuse parent models dir if it exists) ─────────────────
_models_dir = BASE_DIR.parent / "models"
HF_HOME = str(_models_dir) if _models_dir.exists() else str(BASE_DIR / "models")
os.environ.setdefault("HF_HOME", HF_HOME)
