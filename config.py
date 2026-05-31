"""Document AI Expert — Central Configuration.

Mode flags are driven by environment variables set by start.sh:
  HF_MODE=1    → HuggingFace Spaces low-resource mode (CPU, small models, no Neo4j)
  ADMIN_MODE=0 → Disable admin controls (set by -noadmin CLI switch)
"""
import os
from pathlib import Path
import contextvars

BASE_DIR = Path(__file__).parent
current_session = contextvars.ContextVar("current_session", default="admin")

# ── Operating Mode Flags ───────────────────────────────────────────────────────
# HF_MODE: True when running in HuggingFace Spaces or via `python app.py -hf`
# Also triggers for SPACE_ID env var (native HF Spaces detection)
_hf_space   = bool(os.getenv("SPACE_ID"))
HF_MODE     = bool(os.getenv("HF_MODE")) or _hf_space

# ADMIN_MODE: False disables all admin API routes and UI controls.
# Defaults to True (admin enabled) unless explicitly set to "0" or "false".
_admin_env  = os.getenv("ADMIN_MODE", "1").lower()
ADMIN_MODE  = _admin_env not in ("0", "false", "no")

# ── LLM generation server ──────────────────────────────────────────────────────
LLM_BASE_URL        = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8002")
LLM_COMPLETIONS_URL = f"{LLM_BASE_URL}/v1/completions"

# Model selection:
#   HF mode  → Qwen2.5-1.5B-Instruct (~3 GB RAM, CPU-friendly, good quality)
#   GPU mode → Qwen3-8B (4-bit NF4 quantised, ~5.5 GB VRAM)
_default_model   = "Qwen/Qwen2.5-1.5B-Instruct" if HF_MODE else "Qwen/Qwen3-8B"
LLM_MODEL_ID     = os.getenv("LLM_MODEL_ID",      _default_model)
LLM_MODEL_FILENAME = os.getenv("LLM_MODEL_FILENAME", "")

# Token limits: lower in HF mode to keep CPU inference under ~60 s
_default_max_tokens = "1024" if HF_MODE else "2048"
LLM_MAX_TOKENS   = int(os.getenv("LLM_MAX_TOKENS",   _default_max_tokens))
LLM_TEMPERATURE  = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_TOP_P        = float(os.getenv("LLM_TOP_P",       "0.9"))
LLM_TIMEOUT      = int(os.getenv("LLM_TIMEOUT",      "300"))

# ── Embedding server ───────────────────────────────────────────────────────────
EMBED_BASE_URL       = os.getenv("EMBED_BASE_URL",  "http://127.0.0.1:8003")
EMBED_EMBEDDINGS_URL = f"{EMBED_BASE_URL}/v1/embeddings"

# Embedding model:
#   HF mode  → bge-small-en-v1.5 (~130 MB, 384 dim) — fits alongside LLM in 12 GB
#   GPU mode → bge-m3 (fp16, ~2.5 GB VRAM) — full multilingual, dense+sparse+colbert
_default_embed_model = "BAAI/bge-small-en-v1.5" if HF_MODE else "BAAI/bge-m3"
EMBEDDING_MODEL      = os.getenv("EMBEDDING_MODEL", _default_embed_model)

# Batch size: smaller in HF mode to avoid RAM spikes during ingestion
_default_batch = "2" if HF_MODE else "12"
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", _default_batch))
EMBEDDING_TIMEOUT    = int(os.getenv("EMBEDDING_TIMEOUT",    "120"))

# KV-cache precompilation: disabled in HF mode (holds full KB text in RAM)
KV_CACHE_ENABLED     = not HF_MODE

# ── ChromaDB & Security ────────────────────────────────────────────────────────
CHROMA_PERSIST_DIR  = os.getenv("CHROMA_PERSIST_DIR", str(BASE_DIR / "data" / "chroma_db"))
CHROMA_COLLECTION   = os.getenv("CHROMA_COLLECTION",  "Document")
ENCRYPTION_KEY_FILE = os.getenv("ENCRYPTION_KEY_FILE", str(BASE_DIR / "data" / "security.key"))

# ── Neo4j ──────────────────────────────────────────────────────────────────────
NEO4J_URI       = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER      = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD  = os.getenv("NEO4J_PASSWORD", "healthexpert")
# Neo4j requires Docker-in-Docker which is unavailable on HF Spaces
NEO4J_AVAILABLE = not HF_MODE

# ── Flask / Upload ─────────────────────────────────────────────────────────────
UPLOAD_FOLDER       = os.getenv("UPLOAD_FOLDER", str(BASE_DIR / "uploads"))
MAX_CONTENT_LENGTH  = 1 * 1024 * 1024  # 1 MB limit
ALLOWED_EXTENSIONS  = {".txt", ".pdf", ".docx", ".xlsx", ".csv", ".png", ".jpg", ".jpeg", ".webp"}
SECRET_KEY          = os.getenv("SECRET_KEY", "healthexpert-dev-key-change-in-prod")

# ── RAG ────────────────────────────────────────────────────────────────────────
CHUNK_SIZE    = int(os.getenv("CHUNK_SIZE",    "512"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "64"))
TOP_K_VECTOR  = int(os.getenv("TOP_K_VECTOR",  "5"))
TOP_K_GRAPH   = int(os.getenv("TOP_K_GRAPH",   "5"))

# ── HuggingFace model cache ────────────────────────────────────────────────────
_models_dir = BASE_DIR.parent / "models"
HF_HOME     = str(_models_dir) if _models_dir.exists() else str(BASE_DIR / "models")
os.environ.setdefault("HF_HOME", HF_HOME)

# ── System info (for resource banner) ─────────────────────────────────────────
SYSTEM_INFO_ENABLED = True   # /api/sysinfo endpoint always active