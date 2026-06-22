# app.py - healthexpert UI

"""Document AI Expert — Flask Application."""
from __future__ import annotations
import sys

# ── CLI switch parsing (must happen BEFORE config import) ─────────────────────
# -hf      → sets HF_MODE=1 (low-resource CPU mode)
# -noadmin → sets ADMIN_MODE=0 (disables admin routes and UI controls)
for _arg in sys.argv[1:]:
    if _arg in ("-hf", "--hf"):
        import os as _os
        _os.environ["HF_MODE"] = "1"
    elif _arg in ("-noadmin", "--noadmin"):
        import os as _os
        _os.environ["ADMIN_MODE"] = "0"

import os
os.environ["PYTHONWARNINGS"] = "ignore"
# Suppress HuggingFace Hub unauthenticated-request noise before any imports
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
import uuid, json, threading, subprocess, logging, warnings, signal, time
from pathlib import Path

warnings.filterwarnings("ignore", category=ImportWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message=".*register_constant.*")
warnings.filterwarnings("ignore", message=".*Enum subclass.*")
warnings.filterwarnings("ignore", message=".*unauthenticated.*")
from flask import Flask, render_template, request, jsonify, Response, stream_with_context

import sys
sys.path.insert(0, str(Path(__file__).parent))
import config
from pipeline import vector_store, graph_store, embedder, document_loader, chunker
from agents.crew import run_ingest_crew, run_query_crew

log = logging.getLogger("app")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [app] %(levelname)s %(message)s")

# ── Silence noisy third-party loggers ─────────────────────────────────────────
for _quiet in (
    "werkzeug",
    "numexpr",
    "httpx",
    "filelock",
    "pikepdf",
    "pikepdf._core",
    "unstructured",
    "unstructured.partition",
    "unstructured.partition.pdf",
    "pdfminer",
    "pdfminer.pdfdocument",
    "pdfminer.pdfpage",
    "pdfminer.converter",
    "huggingface_hub",
    "huggingface_hub.utils",
    "huggingface_hub.utils._validators",
    "transformers",
    "sentence_transformers",
    "detectron2",
    "pytesseract",
    "PIL",
    "torch",
    "torch.utils",
    "torch.utils._pytree",
):
    logging.getLogger(_quiet).setLevel(logging.ERROR)

# Capture all Python warnings and route them to the py.warnings logger, then silence it
logging.captureWarnings(True)
logging.getLogger("py.warnings").setLevel(logging.ERROR)

# ── App setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH
os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)

# F1: Flask-Limiter rate limiting
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.utils import escape  # F11
from huggingface_hub import HfApi, hf_hub_download

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "10 per minute"],
    storage_uri="memory://"
)

# F2: Strict HTTP security headers
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Content-Security-Policy'] = "default-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://fonts.googleapis.com https://fonts.gstatic.com;"
    return response

# Global HF API for async log push
_hf_api = None
def get_hf_api():
    global _hf_api
    if _hf_api is None:
        token = os.environ.get("HF_PRIVATE_TOKEN") or os.environ.get("HF_TOKEN")
        if token:
            _hf_api = HfApi(token=token)
    return _hf_api

def async_sync_log(local_path: str, repo_path: str):
    """Async-push a log file to Sam-max1/mat_data dataset."""
    def _upload():
        api = get_hf_api()
        if api:
            try:
                api.upload_file(
                    path_or_fileobj=local_path,
                    path_in_repo=repo_path,
                    repo_id="Sam-max1/mat_data",
                    repo_type="dataset"
                )
            except Exception as e:
                log.warning(f"Failed to push {repo_path} to mat_data: {e}")
    threading.Thread(target=_upload, daemon=True).start()

# In-memory job tracker for async ingestion
_jobs: dict[str, dict] = {}
_active_graph_tasks = 0
_session_uploads: dict[str, int] = {}

# Async query job tracker (F3)
_query_jobs: dict[str, dict] = {}
from concurrent.futures import ThreadPoolExecutor
_query_executor = ThreadPoolExecutor(max_workers=2)

# Auto-ingest background progress tracker
_auto_ingest_status: dict = {
    "running": False,
    "done": False,
    "total": 0,
    "completed": 0,
    "current_file": None,
    "results": [],
    "error": None,
}

# RBAC Session Tracking
_active_sessions: dict[str, float] = {}
_known_sessions: dict[str, str] = {}  # token → IP (F10)
SESSION_TIMEOUT_SECONDS = 600  # 10 minutes

def _allowed(filename: str) -> bool:
    return Path(filename).suffix.lower() in config.ALLOWED_EXTENSIONS

def is_admin() -> bool:
    """Return True if the request comes from an admin-privileged context.
    
    In HF mode with ADMIN_MODE=1: admin is granted to all localhost requests.
    With ADMIN_MODE=0 (-noadmin): always False — no admin access regardless of IP.
    """
    if not config.ADMIN_MODE:
        return False
    if config.HF_MODE:
        # In HF mode, admin is only valid from the loopback (e.g. start.sh itself)
        return request.remote_addr in ("127.0.0.1", "::1")
    return request.remote_addr in ("127.0.0.1", "::1", "localhost")

@app.before_request
def block_external_apis():
    """Hard block all external API (headless) access in public mode."""
    if not config.ADMIN_MODE:
        if request.path.startswith("/api/v1/"):
            return jsonify({"error": "Headless API access is disabled in public mode."}), 403

# ── Telemetry helpers (F10 / F9) ─────────────────────────────────────────────

def log_session(event_type: str, token: str, ip: str):
    try:
        log_dir = Path(__file__).parent / "app" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        session_file = log_dir / "nitdaa_sessions.json"
        from datetime import datetime, timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        ts = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")
        entry = {"timestamp": ts, "event": event_type, "session_token": token, "ip_address": ip}
        with open(session_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
        async_sync_log(str(session_file), "nitdaa_sessions.json")
    except Exception as e:
        log.error(f"Failed to log session: {e}")

def log_query_summary(token: str, ip: str, query: str, chunks_retrieved: int, gen_time: float, success: bool, error: str = "", job_id: str = ""):
    try:
        log_dir = Path(__file__).parent / "app" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        summary_file = log_dir / "nitdaa_summary.json"
        from datetime import datetime, timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        ts = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "timestamp": ts, "job_id": job_id, "ip_address": ip,
            "session_token": token, "question": query,
            "chunks_retrieved": chunks_retrieved,
            "generation_time_sec": gen_time, "success": success, "error": error
        }
        with open(summary_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
        async_sync_log(str(summary_file), "nitdaa_summary.json")
    except Exception as e:
        log.error(f"Failed to log query summary: {e}")

def get_session_token() -> str:
    """Return session token if the user is not an admin, else 'admin'."""
    if is_admin():
        return "admin"
    token = request.headers.get("X-Session-Token") or request.form.get("session_token")
    if not token and request.json:
        token = request.json.get("session_token")
    if not token:
        token = "anonymous"
    ip = request.remote_addr
    # F10: log first-seen CONNECT
    if token not in _known_sessions and token not in ("admin", "anonymous"):
        _known_sessions[token] = ip
        log_session("CONNECT", token, ip)
    _active_sessions[token] = time.time()
    return token

def trigger_kv_cache_update(session_token: str = "admin"):
    """Fetches all text and sends it to gen_llm to update KV cache."""
    def _update(token):
        from pipeline import vector_store
        import requests
        text = vector_store.get_all_text(session_token=token)
        log.info("Triggering KV cache update with %d chars...", len(text))
        try:
            requests.post(f"{config.LLM_BASE_URL}/v1/kv_cache", json={"text": text}, timeout=120)
            log.info("KV Cache updated successfully.")
        except Exception as e:
            log.error("Failed to update KV Cache: %s", e)
    threading.Thread(target=_update, args=(session_token,), daemon=True).start()


def _run_docker(action: str) -> tuple[bool, str]:
    """Run docker compose action ('up', 'down', 'restart') and return (ok, message)."""
    compose_file = str(Path(__file__).parent / "docker-compose.yml")
    cmd_map = {
        "up":      ["docker", "compose", "-f", compose_file, "up", "-d"],
        "down":    ["docker", "compose", "-f", compose_file, "down"],
        "restart": ["docker", "compose", "-f", compose_file, "restart"],
    }
    cmd = cmd_map.get(action)
    if cmd is None:
        return False, f"Unknown action: {action}"
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        ok  = result.returncode == 0
        out = (result.stdout + result.stderr).strip()
        log.info("docker compose %s → rc=%d  %s", action, result.returncode, out[:200])
        return ok, out or ("OK" if ok else "Command returned non-zero exit code")
    except subprocess.TimeoutExpired:
        return False, "docker compose timed out after 60 s"
    except FileNotFoundError:
        return False, "docker binary not found — ensure Docker is installed"
    except Exception as exc:
        return False, str(exc)


# ── Graceful Shutdown ─────────────────────────────────────────────────────────

def _graceful_shutdown(signum, frame):
    log.error(f"Received signal {signum}. Triggering kill switch for graceful shutdown...")
    _run_docker("down")
    import time
    time.sleep(1)
    os._exit(0)

signal.signal(signal.SIGINT, _graceful_shutdown)
signal.signal(signal.SIGTERM, _graceful_shutdown)


# ── Background Cleanup Agent ──────────────────────────────────────────────────

def _cleanup_agent():
    while True:
        time.sleep(60)
        now = time.time()
        expired = [token for token, last_active in list(_active_sessions.items())
                   if token != "admin" and token != "anonymous" and (now - last_active) > SESSION_TIMEOUT_SECONDS]
        for token in expired:
            log.info(f"Cleanup Agent: Session '{token}' inactive for 10 mins. Purging data...")
            # F10: log DISCONNECT before purging
            if token in _known_sessions:
                log_session("DISCONNECT", token, _known_sessions[token])
                del _known_sessions[token]
            vector_store.delete_by_session(token)
            graph_store.delete_by_session(token)
            del _active_sessions[token]
            trigger_kv_cache_update(token)

threading.Thread(target=_cleanup_agent, daemon=True).start()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", config=config)


@app.route("/api/status")
@limiter.exempt  # F12: exempt status polling from rate limits
def status():
    """Health check for all backends."""
    vec_count  = vector_store.count()
    graph_stat = graph_store.get_stats()

    # Probe gen_llm
    import requests as req
    gen_ok, embed_ok = False, False
    gen_info = {}
    try:
        r = req.get(f"{config.LLM_BASE_URL}/health", timeout=3)
        gen_ok = r.status_code == 200
        if gen_ok:
            gen_info = r.json()
    except req.exceptions.ReadTimeout:
        # LLM is busy generating, which is fine
        gen_ok = True
        gen_info = {"status": "busy", "model": config.LLM_MODEL_ID}
    except Exception as e:
        log.warning("Gen LLM status check failed: %s", e)
        
    try:
        r = req.get(f"{config.EMBED_BASE_URL}/health", timeout=3)
        embed_ok = r.status_code == 200
    except req.exceptions.ReadTimeout:
        embed_ok = True
    except Exception as e:
        log.warning("Embed LLM status check failed: %s", e)

    return jsonify({
        "vector_db":  {"status": "ok", "chunks": vec_count},
        "graph_db":   graph_stat,
        "gen_llm":    {
            "endpoint": config.LLM_BASE_URL,
            "online":   gen_ok,
            "model":    "-".join(gen_info.get("model", config.LLM_MODEL_ID).split("-")[:2]) if "-" in gen_info.get("model", config.LLM_MODEL_ID) else gen_info.get("model", config.LLM_MODEL_ID),
            "gpu_id":   gen_info.get("gpu_id", "cpu"),
            "kv_cache_length": gen_info.get("kv_cache_length", 0),
        },
        "embed_llm":  {
            "endpoint": config.EMBED_EMBEDDINGS_URL,
            "model":    config.EMBEDDING_MODEL,
            "online":   embed_ok,
        },
        "is_admin":   is_admin(),
        "hf_mode":    config.HF_MODE,
        "admin_mode": config.ADMIN_MODE,
    })


@app.route("/api/sysinfo")
def sysinfo():
    """System resource info for the UI resource banner.
    Returns CPU model/count, load %, RAM used/total (GB), disk free/total (GB).
    """
    try:
        import psutil
        mem  = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        cpu_freq = psutil.cpu_freq()

        # RAM in GB
        ram_total_gb = round(mem.total      / 1024 ** 3, 1)
        ram_used_gb  = round((mem.total - mem.available) / 1024 ** 3, 1)
        ram_pct      = mem.percent

        # Disk in GB
        disk_total_gb = round(disk.total / 1024 ** 3, 1)
        disk_free_gb  = round(disk.free  / 1024 ** 3, 1)
        disk_pct      = round(disk.percent, 1)

        # CPU
        cpu_pct   = psutil.cpu_percent(interval=0.2)
        cpu_count = psutil.cpu_count(logical=True)
        cpu_phys  = psutil.cpu_count(logical=False) or cpu_count

        # CPU brand (Linux: read /proc/cpuinfo)
        cpu_brand = "CPU"
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        cpu_brand = line.split(":", 1)[1].strip()
                        # Shorten common long strings
                        cpu_brand = cpu_brand.replace("(R)", "").replace("(TM)", "").strip()
                        break
        except Exception:
            pass

        cpu_mhz = round(cpu_freq.current, 0) if cpu_freq else None

        # GPU availability detection
        gpu_available = False
        try:
            import torch
            gpu_available = torch.cuda.is_available()
        except Exception:
            pass

        return jsonify({
            "cpu_brand":    cpu_brand,
            "cpu_cores":    cpu_count,
            "cpu_phys":     cpu_phys,
            "cpu_mhz":      cpu_mhz,
            "cpu_pct":      cpu_pct,
            "ram_total_gb": ram_total_gb,
            "ram_used_gb":  ram_used_gb,
            "ram_pct":      ram_pct,
            "disk_total_gb": disk_total_gb,
            "disk_free_gb": disk_free_gb,
            "disk_pct":     disk_pct,
            "hf_mode":      config.HF_MODE,
            "active_graph_tasks": _active_graph_tasks,
            "gpu_available": gpu_available,
        })
    except Exception as exc:
        log.warning("sysinfo failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@app.route("/api/documents")
def list_documents():
    token = get_session_token()
    docs = vector_store.list_documents(session_token=token)
    return jsonify({"documents": docs, "total": len(docs)})


# ── Admin Controls ────────────────────────────────────────────────────────────

@app.route("/api/docker/<action>", methods=["POST"])
def docker_control(action: str):
    """Control Kuzu docker container. action: up | down | restart"""
    if not config.ADMIN_MODE:
        return jsonify({"error": "Admin mode is disabled on this deployment."}), 403
    if not is_admin():
        return jsonify({"error": "Only admins can control docker containers."}), 403
    if action not in ("up", "down", "restart"):
        return jsonify({"error": f"Unknown action '{action}'. Use: up, down, restart"}), 400
    log.info("Docker action requested: %s", action)
    ok, msg = _run_docker(action)
    return jsonify({"ok": ok, "action": action, "output": msg}), (200 if ok else 500)

@app.route("/api/admin/purge", methods=["POST"])
def admin_purge():
    """Wipe all databases clean."""
    if not config.ADMIN_MODE:
        return jsonify({"error": "Admin mode is disabled on this deployment."}), 403
    if not is_admin():
        return jsonify({"error": "Admin only"}), 403
    try:
        vector_store.purge()
        graph_store.purge()
        global _jobs
        _jobs.clear()
        trigger_kv_cache_update("admin")
        log.warning("Admin triggered database purge.")
        return jsonify({"ok": True, "msg": "Databases purged successfully."})
    except Exception as e:
        log.error("Failed to purge databases: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/admin/kill", methods=["POST"])
def admin_kill():
    """Abruptly stop Docker containers and terminate the Flask application."""
    if not config.ADMIN_MODE:
        return jsonify({"error": "Admin mode is disabled on this deployment."}), 403
    if not is_admin():
        return jsonify({"error": "Admin only"}), 403
        
    log.error("KILL SWITCH ACTIVATED. Shutting down docker and terminating process.")
    _run_docker("down")
    
    def _shutdown():
        import time
        time.sleep(1)  # Allow HTTP response to send
        os._exit(0)
    threading.Thread(target=_shutdown, daemon=True).start()
    return jsonify({"ok": True, "msg": "Kill switch activated. Application terminating."})


# ── Ingestion ─────────────────────────────────────────────────────────────────

def _extract_entities_async(
    docs: list[dict],
    orig_name: str,
    tier: str,
    token: str,
) -> None:
    """Fire-and-forget entity extraction → Kuzu graph using fast local spaCy pipeline (non-LLM)."""
    if not graph_store.is_available():
        return
        
    global _active_graph_tasks
    _active_graph_tasks += 1
    try:
        import spacy
        try:
            nlp = spacy.load("en_core_web_sm")
        except OSError:
            log.warning("spaCy model 'en_core_web_sm' not found. Attempting to download...")
            try:
                import spacy.cli
                spacy.cli.download("en_core_web_sm")
                nlp = spacy.load("en_core_web_sm")
            except Exception as e:
                log.error("Failed to download or load spaCy model 'en_core_web_sm': %s. Graph extraction skipped.", e)
                return

        text = "\n\n".join(d["text"] for d in docs)
        
        # spaCy max length limit
        if len(text) > 1000000:
            text = text[:1000000]

        log.info("Entity extraction (spaCy) starting for %s...", orig_name)
        doc = nlp(text)
        
        entities = []
        # Group entities by sentence to establish co-occurrence relationships
        for sent in doc.sents:
            # Filter for specific entity types
            sent_ents = [ent for ent in sent.ents if ent.label_ in {"PERSON", "ORG", "GPE", "LOC", "FAC", "PRODUCT", "EVENT", "WORK_OF_ART", "LAW"}]
            if not sent_ents:
                continue
            
            # Map spaCy labels to our schema types
            def _map_type(label: str) -> str:
                if label == "PERSON": return "Person"
                if label == "ORG": return "Organization"
                if label in {"GPE", "LOC", "FAC"}: return "Location"
                if label == "EVENT": return "Event"
                if label == "PRODUCT": return "Object"
                if label in {"WORK_OF_ART", "LAW"}: return "Rule"
                return "Concept"
            
            # Create entity objects and cross-link within the same sentence
            for i, ent1 in enumerate(sent_ents):
                name1 = ent1.text.strip()
                if not name1 or len(name1) < 2:
                    continue
                
                relations = []
                for j, ent2 in enumerate(sent_ents):
                    if i != j:
                        name2 = ent2.text.strip()
                        if name2 and name2 != name1:
                            relations.append({"target": name2, "rel": "RELATED_TO"})
                
                # Deduplicate relations
                unique_rels = []
                seen_targets = set()
                for r in relations:
                    if r["target"] not in seen_targets:
                        seen_targets.add(r["target"])
                        unique_rels.append(r)
                
                entities.append({
                    "name": name1,
                    "type": _map_type(ent1.label_),
                    "relations": unique_rels
                })

        # Deduplicate the entities list by name before sending to Kuzu
        dedup_entities = {}
        for ent in entities:
            if ent["name"] not in dedup_entities:
                dedup_entities[ent["name"]] = ent
            else:
                # Merge relations
                existing_rels = {r["target"] for r in dedup_entities[ent["name"]]["relations"]}
                for rel in ent["relations"]:
                    if rel["target"] not in existing_rels:
                        dedup_entities[ent["name"]]["relations"].append(rel)
                        existing_rels.add(rel["target"])

        final_entities = list(dedup_entities.values())

        if final_entities:
            graph_store.store_entities(final_entities, orig_name, tier=tier, session_token=token)
            log.info("Entity extraction (spaCy) for %s: %d unique entities stored in Kuzu", orig_name, len(final_entities))
        else:
            log.info("Entity extraction (spaCy) for %s: No entities found", orig_name)

    except Exception as exc:
        log.warning("Entity extraction background task failed for %s: %s", orig_name, exc)
    finally:
        _active_graph_tasks -= 1

def process_document_pipeline(path: str, orig_name: str, tier: str, token: str, delete_after: bool = True) -> dict:
    step_log = []
    added = 0
    try:
        step_log.append(f"[{orig_name}] Starting ingestion pipeline…")
        log.info("Ingesting %s", orig_name)

        # Step 1: load
        step_log.append(f"[{orig_name}] Loading document…")
        docs = document_loader.load_document(path)
        step_log.append(f"[{orig_name}] Loaded {len(docs)} page(s).")
        log.info("%s loaded — %d pages", orig_name, len(docs))

        # Step 2: chunk
        step_log.append(f"[{orig_name}] Chunking…")
        chunks = chunker.chunk_documents(docs)
        if not chunks:
            raise ValueError("No text could be extracted from this document.")
        step_log.append(f"[{orig_name}] Created {len(chunks)} chunks.")
        log.info("%s → %d chunks", orig_name, len(chunks))

        # Step 3: embed
        step_log.append(f"[{orig_name}] Embedding via embed_llm (port 8003)…")
        texts      = [c["text"] for c in chunks]
        embeddings = embedder.embed_texts(texts)
        step_log.append(f"[{orig_name}] Embedded {len(embeddings)} vectors (dim={len(embeddings[0]) if embeddings else '?'}).")
        log.info("%s embedded", orig_name)

        # Step 4: store in vector DB
        step_log.append(f"[{orig_name}] Storing in ChromaDB (tier: {tier}, session: {token})…")
        if config.HF_MODE and vector_store.count() + len(chunks) > 10000:
            allowed = 10000 - vector_store.count()
            if allowed <= 0:
                raise ValueError("Vector database full (10000 chunk limit).")
            chunks = chunks[:allowed]
            embeddings = embeddings[:allowed]
            step_log.append(f"[{orig_name}] WARNING: Truncated to {allowed} chunks due to global 10000 chunk limit.")

        doc_id  = uuid.uuid4().hex[:8]
        added   = vector_store.add_chunks(chunks, embeddings, doc_id, tier=tier, session_token=token)
        step_log.append(f"[{orig_name}] Stored {added} chunks in vector DB (doc_id={doc_id}).")
        log.info("%s stored %d chunks in ChromaDB", orig_name, added)

        # Step 5: entity extraction → graph (non-blocking — runs in daemon thread)
        if graph_store.is_available():
            step_log.append(f"[{orig_name}] Entity extraction queued (background thread)…")
            threading.Thread(
                target=_extract_entities_async,
                args=(docs, orig_name, tier, token),
                daemon=True,
                name=f"entity-{orig_name[:20]}",
            ).start()
        else:
            step_log.append(f"[{orig_name}] Kuzu offline — graph extraction skipped.")

        return {"ok": True, "result": f"Ingested {added} chunks", "log": step_log, "added": added}
    except Exception as exc:
        step_log.append(f"[{orig_name}] ERROR: {exc}")
        log.exception("Ingestion failed for %s", orig_name)
        return {"ok": False, "result": str(exc), "log": step_log, "added": added}
    finally:
        if delete_after and os.path.exists(path):
            try:
                os.remove(path)
                log.info("Deleted local upload file: %s", path)
            except OSError as e:
                log.warning("Failed to delete %s: %s", path, e)


@app.route("/api/ingest", methods=["POST"])
def ingest():
    """Upload and asynchronously ingest one or more documents."""
    log.info("Ingest request received. Files in request: %s",
             list(request.files.keys()))

    if "files" not in request.files:
        log.warning("No 'files' key in request.files")
        return jsonify({"error": "No files uploaded — send a multipart/form-data POST with field name 'files'"}), 400

    files = request.files.getlist("files")
    tier = request.form.get("tier", "extended")
    token = get_session_token()
    log.info("Received %d file(s): %s to tier: %s (session: %s)", len(files), [f.filename for f in files], tier, token)

    if tier == "foundation" and not is_admin():
        return jsonify({"error": "Only admins can upload to the Foundation tier."}), 403

    if not files or all(not f.filename for f in files):
        return jsonify({"error": "File list is empty or filenames are blank"}), 400

    # ── Security Limits ──
    if config.HF_MODE:
        current_uploads = _session_uploads.get(token, 0)
        if current_uploads + len(files) > 5:
            return jsonify({"error": f"Session limit exceeded. You can only upload 5 files per session. (Current: {current_uploads})"}), 429
            
        current_chunks = vector_store.count()
        if current_chunks >= 10000:
            return jsonify({"error": "Vector database is full (10000 chunk limit reached). Please wait for an admin to purge."}), 429
            
        _session_uploads[token] = current_uploads + len(files)
        
    job_id      = uuid.uuid4().hex[:8]
    saved_paths = []
    rejected    = []

    for f in files:
        if not f.filename:
            rejected.append("(unnamed file)")
            continue
        if not _allowed(f.filename):
            ext = Path(f.filename).suffix or "(no extension)"
            rejected.append(f"{f.filename} — unsupported type '{ext}'")
            log.warning("Rejected file %s — extension not in ALLOWED_EXTENSIONS", f.filename)
            continue
        dest_dir = Path(__file__).parent / "kbdocs"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = os.path.join(str(dest_dir), Path(f.filename).name)
        try:
            f.save(dest)
            file_size = os.path.getsize(dest)
            log.info("Saved %s → %s  (%d bytes)", f.filename, dest, file_size)
            saved_paths.append((dest, f.filename))
        except Exception as exc:
            rejected.append(f"{f.filename} — save failed: {exc}")
            log.error("Failed to save %s: %s", f.filename, exc)

    if not saved_paths:
        msg = "No valid files found."
        if rejected:
            msg += " Rejected: " + "; ".join(rejected)
        log.error("Ingest aborted — %s", msg)
        return jsonify({"error": msg}), 400

    _jobs[job_id] = {
        "status":   "running",
        "results":  [],
        "total":    len(saved_paths),
        "rejected": rejected,
        "log":      [],
    }
    log.info("Job %s created for %d file(s)", job_id, len(saved_paths))

    def _worker(sess_token):
        config.current_session.set(sess_token)
        for path, orig_name in saved_paths:
            res = process_document_pipeline(path, orig_name, tier, token, delete_after=False)
            res["file"] = orig_name
            _jobs[job_id]["results"].append(res)
            _jobs[job_id]["log"].extend(res["log"])

        _jobs[job_id]["status"] = "done"
        log.info("Job %s complete — %d results", job_id,
                 len(_jobs[job_id]["results"]))
        trigger_kv_cache_update(sess_token)

    threading.Thread(target=_worker, args=(token,), daemon=True).start()
    return jsonify({
        "job_id":   job_id,
        "files":    [p[1] for p in saved_paths],
        "rejected": rejected,
    })


@app.route("/api/ingest/status/<job_id>")
def ingest_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return jsonify({"error": "Unknown job"}), 404
    return jsonify(job)


@app.route("/api/documents/<path:source_name>", methods=["DELETE"])
def delete_document(source_name: str):
    tier = request.args.get("tier", "extended")
    log.info("Delete request for: %s (tier: %s)", source_name, tier)
    
    if tier == "foundation" and not is_admin():
        return jsonify({"error": "Only admins can delete from the Foundation tier."}), 403
        
    token = get_session_token()
    
    deleted_vec = vector_store.delete_document(source_name, session_token=token)
    graph_store.delete_source(source_name, session_token=token)
    log.info("Deleted %d chunks for '%s'", deleted_vec, source_name)
    
    trigger_kv_cache_update(token)
    
    return jsonify({"deleted_chunks": deleted_vec, "source": source_name})


# ── Query ─────────────────────────────────────────────────────────────────────

@app.route("/api/query", methods=["POST"])
def query():
    """RAG query — legacy blocking SSE response (kept for backwards compatibility)."""
    data = request.get_json()
    q    = (data or {}).get("query", "").strip()
    top_k = (data or {}).get("top_k")
    max_tokens = (data or {}).get("max_tokens")
    use_vector = (data or {}).get("use_vector", True)
    use_graph  = bool((data or {}).get("use_graph", True))
    use_bm25   = bool((data or {}).get("use_bm25", True))
    use_gpu = bool((data or {}).get("use_gpu", False))
    cpu_threads = int((data or {}).get("cpu_threads", 2))
    llm_mode    = (data or {}).get("llm_mode",    "expert")   # F4: expert/assistant
    llm_backend = (data or {}).get("llm_backend", "local")    # F24: local/nvidia
    if not q:
        return jsonify({"error": "Empty query"}), 400

    token = get_session_token()

    chunk_count = vector_store.count()
    if chunk_count == 0:
        return jsonify({"error": "No documents ingested yet. Please upload documents first."}), 400

    log.info("Query received (%d chars) | vector store has %d chunks", len(q), chunk_count)

    def _generate(sess_token):
        import queue
        q_events = queue.Queue()

        def _run():
            config.current_session.set(sess_token)
            try:
                def cb(status):
                    if isinstance(status, dict):
                        q_events.put(status)
                    else:
                        q_events.put({"status": status})
                ans, metrics = run_query_crew(q, top_k=top_k, max_tokens=max_tokens, use_vector=use_vector, use_graph=use_graph, use_bm25=use_bm25, session_token=token, status_callback=cb, use_gpu=use_gpu, cpu_threads=cpu_threads, llm_mode=llm_mode, llm_backend=llm_backend)
                q_events.put({"done": True, "answer": ans, "metrics": metrics})
            except Exception as e:
                log.exception("Query pipeline error")
                q_events.put({"error": str(e)})

        threading.Thread(target=_run, daemon=True).start()

        while True:
            event = q_events.get()
            if "error" in event:
                yield f"data: {json.dumps({'error': event['error']})}\n\n"
                break
            elif "status" in event:
                yield f"data: {json.dumps({'status': event['status']})}\n\n"
            elif "done" in event:
                answer = event["answer"]
                log.info("Query answered — %d chars", len(answer))
                for i in range(0, len(answer), 80):
                    chunk   = answer[i:i + 80]
                    payload = json.dumps({"chunk": chunk})
                    yield f"data: {payload}\n\n"
                yield f"data: {json.dumps({'metrics': event['metrics']})}\n\n"
                yield "data: {\"done\": true}\n\n"
                break

    return Response(
        stream_with_context(_generate(token)),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# F3: Async Job-ID query system + F4: Dual LLM mode + F11: input escape
@app.route("/api/query/start", methods=["POST"])
@limiter.limit("120 per minute")
def query_start():
    """Starts a RAG query job and returns a job_id for resumable streaming."""
    data = request.get_json()
    q    = escape((data or {}).get("query", "").strip())  # F11
    top_k = (data or {}).get("top_k")
    max_tokens = (data or {}).get("max_tokens")
    use_vector = (data or {}).get("use_vector", True)
    use_graph  = bool((data or {}).get("use_graph", True))
    use_bm25   = bool((data or {}).get("use_bm25", True))
    use_gpu = bool((data or {}).get("use_gpu", False))
    cpu_threads = int((data or {}).get("cpu_threads", 2))
    llm_mode    = (data or {}).get("llm_mode",    "expert")   # F4: expert/assistant
    llm_backend = (data or {}).get("llm_backend", "local")    # F24: local/nvidia

    if not q:
        return jsonify({"error": "Empty query"}), 400

    token = get_session_token()
    chunk_count = vector_store.count()
    if chunk_count == 0:
        return jsonify({"error": "No documents ingested yet. Please upload documents first."}), 400

    log.info("Query/start received (%d chars) | vector store has %d chunks", len(q), chunk_count)
    remote_addr = request.remote_addr

    job_id = uuid.uuid4().hex[:8]
    _query_jobs[job_id] = {"events": [], "done": False, "error": None}

    def _run():
        config.current_session.set(token)
        try:
            def cb(status):
                if isinstance(status, dict):
                    _query_jobs[job_id]["events"].append(status)
                else:
                    _query_jobs[job_id]["events"].append({"status": status})
            ans, metrics = run_query_crew(q, top_k=top_k, max_tokens=max_tokens, use_vector=use_vector, use_graph=use_graph, use_bm25=use_bm25, session_token=token, status_callback=cb, use_gpu=use_gpu, cpu_threads=cpu_threads, llm_mode=llm_mode, llm_backend=llm_backend)
            for i in range(0, len(ans), 80):
                _query_jobs[job_id]["events"].append({"chunk": ans[i:i+80]})
            _query_jobs[job_id]["events"].append({"metrics": metrics})
            _query_jobs[job_id]["events"].append({"done": True})
            _query_jobs[job_id]["done"] = True
            gen_time = metrics.get("time_seconds", 0)
            log_query_summary(token, remote_addr, q, top_k or 10, gen_time, True, "", job_id)
        except Exception as e:
            log.exception("Query/start pipeline failed")
            _query_jobs[job_id]["events"].append({"error": str(e)})
            _query_jobs[job_id]["done"] = True
            log_query_summary(token, remote_addr, q, top_k or 10, 0, False, str(e), job_id)

    _query_executor.submit(_run)
    return jsonify({"job_id": job_id})


@app.route("/api/query/stream/<job_id>")
def query_stream(job_id):
    """Streams events for a specific query job starting from an offset (resumable)."""
    offset = int(request.args.get("offset", 0))
    job = _query_jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found or expired"}), 404

    def _generate():
        idx = offset
        while True:
            while idx < len(job["events"]):
                event = job["events"][idx]
                yield f"data: {json.dumps(event)}\n\n"
                if "error" in event or "done" in event:
                    return
                idx += 1
            if job.get("error") or job.get("done"):
                break
            time.sleep(0.5)
            yield ": keep-alive\n\n"

    return Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# F9: Feedback endpoint
@app.route("/api/feedback", methods=["POST"])
@limiter.limit("20 per minute")
def api_feedback():
    """Accept thumbs, stars and text feedback after a response."""
    data = request.get_json() or {}
    job_id = data.get("job_id", "")
    rating = data.get("rating", "")
    stars  = data.get("stars", None)
    text   = data.get("text", "")
    token  = get_session_token()
    try:
        log_dir = Path(__file__).parent / "app" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        summary_file = log_dir / "nitdaa_summary.json"
        from datetime import datetime, timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        ts = datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S")
        entry = {"timestamp": ts, "session_token": token, "job_id": job_id, "type": "feedback"}
        if rating: entry["feedback_rating"] = rating
        if stars is not None: entry["feedback_stars"] = stars
        if text: entry["feedback_text"] = text
        with open(summary_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
        async_sync_log(str(summary_file), "nitdaa_summary.json")
        return jsonify({"ok": True})
    except Exception as e:
        log.error(f"Failed to save feedback: {e}")
        return jsonify({"error": str(e)}), 500


# ── Headless API v1 ───────────────────────────────────────────────────────────

@app.route("/api/v1/query", methods=["POST"])
def query_v1():
    """Headless RAG query — synchronous JSON response."""
    data = request.get_json()
    q    = (data or {}).get("query", "").strip()
    top_k = (data or {}).get("top_k")
    if not q:
        return jsonify({"error": "Empty query"}), 400

    token = get_session_token()
    chunk_count = vector_store.count()
    if chunk_count == 0:
        return jsonify({"error": "No documents ingested yet."}), 400

    log.info("v1 Query received (%d chars) | session: %s", len(q), token)
    config.current_session.set(token)
    try:
        ans, metrics = run_query_crew(q, top_k=top_k, session_token=token)
        return jsonify({"answer": ans, "metrics": metrics})
    except Exception as e:
        log.exception("v1 Query pipeline error")
        return jsonify({"error": str(e)}), 500

@app.route("/api/v1/ingest/sync", methods=["POST"])
def ingest_v1_sync():
    """Headless synchronous document ingestion."""
    if "files" not in request.files:
        return jsonify({"error": "No files uploaded"}), 400

    files = request.files.getlist("files")
    tier = request.form.get("tier", "extended")
    token = get_session_token()

    if tier == "foundation" and not is_admin():
        return jsonify({"error": "Only admins can upload to the Foundation tier."}), 403

    saved_paths = []
    rejected = []
    for f in files:
        if not f.filename: continue
        if not _allowed(f.filename):
            rejected.append(f.filename)
            continue
        dest_dir = Path(__file__).parent / "kbdocs"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = os.path.join(str(dest_dir), Path(f.filename).name)
        f.save(dest)
        saved_paths.append((dest, f.filename))

    if not saved_paths:
        return jsonify({"error": "No valid files", "rejected": rejected}), 400

    config.current_session.set(token)
    results = []

    for path, orig_name in saved_paths:
        try:
            docs = document_loader.load_document(path)
            chunks = chunker.chunk_documents(docs)
            if not chunks:
                raise ValueError("No text extracted")

            texts = [c["text"] for c in chunks]
            embeddings = embedder.embed_texts(texts)

            doc_id = uuid.uuid4().hex[:8]
            added = vector_store.add_chunks(chunks, embeddings, doc_id, tier=tier, session_token=token)

            # Entity extraction is fire-and-forget (non-blocking)
            if graph_store.is_available():
                threading.Thread(
                    target=_extract_entities_async,
                    args=(docs, orig_name, tier, token),
                    daemon=True,
                    name=f"entity-{orig_name[:20]}",
                ).start()

            results.append({
                "file": orig_name,
                "status": "success",
                "chunks_added": added,
                "entities_queued": graph_store.is_available(),
            })
        except Exception as e:
            results.append({"file": orig_name, "status": "error", "error": str(e)})
        finally:
            pass # delete_after is False for these sync uploads

    trigger_kv_cache_update(token)
    return jsonify({"results": results, "rejected": rejected})


# ── LLM probe endpoints (used by default prompt buttons) ─────────────────────

@app.route("/api/probe/gen", methods=["POST"])
def probe_gen():
    """Quick smoke-test for the gen_llm server."""
    import requests as req
    data_req = request.get_json(silent=True) or {}
    llm_backend = data_req.get("llm_backend", "local")
    llm_mode    = data_req.get("llm_mode", "expert")
    
    url = config.LLM_COMPLETIONS_URL
    if llm_backend == "nvidia":
        url = "http://127.0.0.1:8004/v1/completions"

    try:
        payload = {
            "prompt": "Hello, reply with one sentence.",
            "max_tokens": 64,
            "temperature": 0.7,
            "top_p": 0.9,
        }
        if llm_backend == "nvidia":
            payload["llm_mode"] = llm_mode

        r = req.post(
            url,
            json=payload,
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        text = data["choices"][0]["text"].strip()
        return jsonify({"ok": True, "model": data.get("model"), "response": text})
    except Exception as exc:
        log.error("probe_gen failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 502


@app.route("/api/probe/embed", methods=["POST"])
def probe_embed():
    """Quick smoke-test for the embed_llm server."""
    import requests as req
    try:
        r = req.post(
            config.EMBED_EMBEDDINGS_URL,
            json={"input": "Document test sentence."},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        vec  = data["data"][0]["embedding"]
        return jsonify({
            "ok": True,
            "model": data.get("model"),
            "dim": len(vec),
            "sample": vec[:5],
        })
    except Exception as exc:
        log.error("probe_embed failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 502


def start_auto_ingest_thread():
    def _auto_ingest_worker():
        """F8: Drift-aware dataset sync — purges and rebuilds if remote data changed."""
        global _auto_ingest_status
        import requests as req, shutil
        from huggingface_hub import snapshot_download

        token = os.environ.get("HF_PRIVATE_TOKEN") or os.environ.get("HF_TOKEN")

        # Wait for LLM services before doing anything
        log.info("Auto-ingest: waiting for LLM services to boot...")
        for _ in range(30):
            try:
                r1 = req.get(f"{config.EMBED_BASE_URL}/health", timeout=2)
                r2 = req.get(f"{config.LLM_BASE_URL}/health", timeout=2)
                if r1.status_code == 200 and r2.status_code == 200:
                    break
            except Exception:
                pass
            time.sleep(2)
        else:
            log.warning("Auto-ingest aborted: LLM services not online.")
            _auto_ingest_status["error"] = "LLM services not online within 60s"
            _auto_ingest_status["done"] = True
            return

        if not token:
            log.error("HF_TOKEN not set — dataset synchronization skipped.")
            _auto_ingest_status["error"] = "HF Token missing"
            _auto_ingest_status["done"] = True
            return

        # F8 + F7: 2-way log sync on startup
        log_dir = Path(__file__).parent / "app" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            for log_file in ["nitdaa_sessions.json", "nitdaa_summary.json"]:
                local_p = log_dir / log_file
                try:
                    dl_path = hf_hub_download(repo_id="Sam-max1/mat_data", filename=log_file, repo_type="dataset", token=token)
                    if os.path.exists(dl_path):
                        remote_lines = set(open(dl_path).readlines())
                        if local_p.exists():
                            for line in open(local_p).readlines():
                                if line not in remote_lines:
                                    remote_lines.add(line)
                        with open(local_p, "w") as f:
                            for line in sorted(list(remote_lines)):
                                f.write(line)
                        log.info(f"Merged {log_file} from mat_data.")
                except Exception as e:
                    log.warning(f"Could not download {log_file} from mat_data: {e}")
        except Exception as e:
            log.warning(f"Log sync failed: {e}")

        kbdocs_dir = Path(__file__).parent / "kbdocs"
        kbdocs_dir.mkdir(parents=True, exist_ok=True)

        tmp_sync_dir = Path("/tmp/he_data_sync")
        if tmp_sync_dir.exists():
            shutil.rmtree(tmp_sync_dir)
        tmp_sync_dir.mkdir(exist_ok=True)

        log.info("Syncing Sam-max1/he-data to /tmp/he_data_sync...")
        try:
            snapshot_download(
                repo_id="Sam-max1/he-data",
                repo_type="dataset",
                local_dir=str(tmp_sync_dir),
                token=token,
                ignore_patterns=[".git*"]
            )
        except Exception as e:
            log.error(f"Failed to download he-data: {e}")
            _auto_ingest_status["error"] = f"Download failed: {e}"
            _auto_ingest_status["done"] = True
            return

        from pipeline import vector_store as vs, graph_store as gs

        # F8: Compare local vs remote by filename + size
        local_files  = {f.name: f.stat().st_size for f in kbdocs_dir.glob("*.*") if f.is_file()}
        remote_files = {f.name: f.stat().st_size for f in tmp_sync_dir.glob("*.*") if f.is_file()}

        is_different = (set(local_files.keys()) != set(remote_files.keys())) or \
                       any(local_files.get(k) != remote_files.get(k) for k in remote_files)

        if is_different:
            log.info("Drift detected in he-data! Purging DBs and re-syncing kbdocs.")
            vs.purge()
            if gs.is_available():
                gs.purge()
            shutil.rmtree(kbdocs_dir)
            shutil.copytree(tmp_sync_dir, kbdocs_dir)

            files_to_ingest = [f for f in kbdocs_dir.glob("*.*") if f.is_file() and _allowed(f.name)]
            if not files_to_ingest:
                log.info("No valid files to ingest in he-data.")
                _auto_ingest_status["done"] = True
                return

            config.current_session.set("admin")
            _auto_ingest_status["running"] = True
            _auto_ingest_status["total"] = len(files_to_ingest)
            _auto_ingest_status["completed"] = 0
            _auto_ingest_status["results"] = []
            _auto_ingest_status["done"] = False

            for path in files_to_ingest:
                _auto_ingest_status["current_file"] = path.name
                log.info(f"Auto-ingesting: {path.name}")
                res = process_document_pipeline(str(path), path.name, "foundation", "admin", delete_after=False)
                _auto_ingest_status["completed"] += 1
                _auto_ingest_status["results"].append({"file": path.name, "ok": res["ok"], "result": res["result"]})
                if res["ok"]:
                    log.info("Auto-ingest OK: %s", path.name)
                else:
                    log.error("Auto-ingest FAIL: %s — %s", path.name, res["result"])

            _auto_ingest_status["running"] = False
            _auto_ingest_status["done"] = True
            _auto_ingest_status["current_file"] = None
            trigger_kv_cache_update("admin")
            log.info("=== Full Data Re-Ingestion Complete ===")
        else:
            log.info("kbdocs is up-to-date with he-data. No ingestion needed.")
            _auto_ingest_status["done"] = True

        log.info(f"Vector DB Chunks: {vs.count()}")
        if gs.is_available():
            stats = gs.get_stats()
            log.info(f"Kuzu DB Nodes: {stats.get('nodes', 0)}, Edges: {stats.get('edges', 0)}")

    threading.Thread(target=_auto_ingest_worker, daemon=True).start()


@app.route("/api/auto-ingest/status")
@limiter.exempt  # F12: exempt from rate limits — polled frequently by UI
def auto_ingest_status():
    """Return real-time progress of the background kbdocs auto-ingestion."""
    return jsonify(_auto_ingest_status)


if __name__ == "__main__":
    mode_label  = "HF / CPU" if config.HF_MODE else "GPU / Desktop"
    admin_label = "ENABLED" if config.ADMIN_MODE else "DISABLED (public mode)"
    run_port  = int(os.environ.get("PORT", 5050))
    ui_url = f"http://127.0.0.1:{run_port}" if not config.HF_MODE else "<HF Spaces URL>"
    print("=" * 64)
    print("  HealthExpert — Document AI Expert")
    print(f"  UI         : {ui_url}")
    print(f"  Mode       : {mode_label}")
    print(f"  Admin      : {admin_label}")
    print(f"  Gen LLM    : {config.LLM_COMPLETIONS_URL}  [{config.LLM_MODEL_ID}]")
    print(f"  Embed LLM  : {config.EMBED_EMBEDDINGS_URL}  [{config.EMBEDDING_MODEL}]")
    print(f"  ChromaDB   : {config.CHROMA_PERSIST_DIR}  (embedded)")
    print(f"  Kuzu DB    : {config.KUZU_DB_PATH}  (embedded)")
    print(f"  KV Cache   : {'DISABLED (HF mode)' if not config.KV_CACHE_ENABLED else 'ENABLED'}")
    print("=" * 64)
    
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    cert_path = str(Path(__file__).parent / "cert.pem")
    key_path  = str(Path(__file__).parent / "key.pem")
    
    start_auto_ingest_thread()
    
    # SSL: skip in HF mode (HF Spaces handles TLS termination at their proxy)
    if os.path.exists(cert_path) and os.path.exists(key_path) and not config.HF_MODE:
        app.run(host="0.0.0.0", port=run_port, debug=False, threaded=True,
                ssl_context=(cert_path, key_path))
    else:
        if config.HF_MODE:
            log.info("HF mode — running HTTP (TLS handled by HF Spaces proxy).")
        else:
            log.warning("SSL certificates not found — running in HTTP mode.")
        app.run(host="0.0.0.0", port=run_port, debug=False, threaded=True)
