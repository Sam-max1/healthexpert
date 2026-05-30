# app.py - healthexpert UI

"""Document AI Expert — Flask Application."""
from __future__ import annotations
import os
os.environ["PYTHONWARNINGS"] = "ignore"
import uuid, json, threading, subprocess, logging, warnings, signal, time
from pathlib import Path

warnings.filterwarnings("ignore", category=ImportWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
from flask import Flask, render_template, request, jsonify, Response, stream_with_context

import sys
sys.path.insert(0, str(Path(__file__).parent))
import config
from pipeline import vector_store, graph_store, embedder, document_loader, chunker
from agents.crew import run_ingest_crew, run_query_crew

log = logging.getLogger("app")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [app] %(levelname)s %(message)s")
logging.getLogger("werkzeug").setLevel(logging.ERROR)
logging.getLogger("numexpr").setLevel(logging.ERROR)

# Capture all Python warnings and route them to the py.warnings logger, then silence it
logging.captureWarnings(True)
logging.getLogger("py.warnings").setLevel(logging.ERROR)

# ── App setup ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = config.SECRET_KEY
app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH
os.makedirs(config.UPLOAD_FOLDER, exist_ok=True)

# In-memory job tracker for async ingestion
_jobs: dict[str, dict] = {}


# RBAC Session Tracking
_active_sessions: dict[str, float] = {}
SESSION_TIMEOUT_SECONDS = 600  # 10 minutes

def _allowed(filename: str) -> bool:
    return Path(filename).suffix.lower() in config.ALLOWED_EXTENSIONS

def is_admin() -> bool:
    return request.remote_addr in ("127.0.0.1", "::1", "localhost")

def get_session_token() -> str:
    """Return session token if the user is not an admin, else 'admin'."""
    if is_admin():
        return "admin"
    token = request.headers.get("X-Session-Token") or request.form.get("session_token")
    if not token and request.json:
        token = request.json.get("session_token")
    if not token:
        token = "anonymous"
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
            requests.post(f"{config.LLM_BASE_URL}/v1/kv_cache", json={"text": text}, timeout=120, verify=False)
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
        expired = [token for token, last_active in _active_sessions.items() 
                   if token != "admin" and token != "anonymous" and (now - last_active) > SESSION_TIMEOUT_SECONDS]
        for token in expired:
            log.info(f"Cleanup Agent: Session '{token}' inactive for 10 mins. Purging data...")
            vector_store.delete_by_session(token)
            graph_store.delete_by_session(token)
            del _active_sessions[token]
            trigger_kv_cache_update(token)

threading.Thread(target=_cleanup_agent, daemon=True).start()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def status():
    """Health check for all backends."""
    vec_count  = vector_store.count()
    graph_stat = graph_store.get_stats()

    # Probe gen_llm
    import requests as req
    gen_ok, embed_ok = False, False
    gen_info = {}
    try:
        r = req.get(f"{config.LLM_BASE_URL}/health", timeout=3, verify=False)
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
        r = req.get(f"{config.EMBED_BASE_URL}/health", timeout=3, verify=False)
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
            "model":    gen_info.get("model", config.LLM_MODEL_ID),
            "gpu_id":   gen_info.get("gpu_id", "cpu"),
            "kv_cache_length": gen_info.get("kv_cache_length", 0)
        },
        "embed_llm":  {
            "endpoint": config.EMBED_EMBEDDINGS_URL,
            "model":    config.EMBEDDING_MODEL,
            "online":   embed_ok,
        },
        "is_admin": is_admin(),
    })


@app.route("/api/documents")
def list_documents():
    token = get_session_token()
    docs = vector_store.list_documents(session_token=token)
    return jsonify({"documents": docs, "total": len(docs)})


# ── Admin Controls ────────────────────────────────────────────────────────────

@app.route("/api/docker/<action>", methods=["POST"])
def docker_control(action: str):
    """Control Neo4j docker container. action: up | down | restart"""
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
    if not is_admin():
        return jsonify({"error": "Admin only"}), 403
        
    log.error("KILL SWITCH ACTIVATED. Shutting down docker and terminating process.")
    _run_docker("down")
    
    def _shutdown():
        import time
        time.sleep(1) # Allow HTTP response to send
        os._exit(0)
    threading.Thread(target=_shutdown, daemon=True).start()
    return jsonify({"ok": True, "msg": "Kill switch activated. Application terminating."})


# ── Ingestion ─────────────────────────────────────────────────────────────────

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
        safe_name = f"{uuid.uuid4().hex[:6]}_{Path(f.filename).name}"
        dest      = os.path.join(config.UPLOAD_FOLDER, safe_name)
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
            step_log = []
            try:
                step_log.append(f"[{orig_name}] Starting ingestion pipeline…")
                log.info("Job %s: ingesting %s", job_id, orig_name)

                # Step 1: load
                step_log.append(f"[{orig_name}] Loading document…")
                docs = document_loader.load_document(path)
                step_log.append(f"[{orig_name}] Loaded {len(docs)} page(s).")
                log.info("Job %s: %s loaded — %d pages", job_id, orig_name, len(docs))

                # Step 2: chunk
                step_log.append(f"[{orig_name}] Chunking…")
                chunks = chunker.chunk_documents(docs)
                if not chunks:
                    raise ValueError("No text could be extracted from this document.")
                step_log.append(f"[{orig_name}] Created {len(chunks)} chunks.")
                log.info("Job %s: %s → %d chunks", job_id, orig_name, len(chunks))

                # Step 3: embed
                step_log.append(f"[{orig_name}] Embedding via embed_llm (port 8003)…")
                texts      = [c["text"] for c in chunks]
                embeddings = embedder.embed_texts(texts)
                step_log.append(f"[{orig_name}] Embedded {len(embeddings)} vectors (dim={len(embeddings[0]) if embeddings else '?'}).")
                log.info("Job %s: %s embedded", job_id, orig_name)

                # Step 4: store in vector DB
                step_log.append(f"[{orig_name}] Storing in ChromaDB (tier: {tier}, session: {token})…")
                doc_id  = uuid.uuid4().hex[:8]
                added   = vector_store.add_chunks(chunks, embeddings, doc_id, tier=tier, session_token=token)
                step_log.append(f"[{orig_name}] Stored {added} chunks in vector DB (doc_id={doc_id}).")
                log.info("Job %s: %s stored %d chunks in ChromaDB", job_id, orig_name, added)

                # Step 5: entity extraction → graph
                if graph_store.is_available():
                    step_log.append(f"[{orig_name}] Extracting entities via gen_llm (port 8002)…")
                    sample_text = "\n\n".join(d["text"] for d in docs[:3])[:3000]
                    from agents.llm import get_llm
                    llm = get_llm()
                    prompt = (
                        "Extract key entities from the text below.\n"
                        "Return a JSON array of objects with keys: name, type, relations.\n"
                        "type must be a broad category like: Person, Organization, Location, Concept, Event, Document, Object, Rule.\n"
                        "relations is a list of {target, rel} objects.\n"
                        "Return ONLY the JSON array.\n\n"
                        f"TEXT:\n{sample_text}\n\nJSON:"
                    )
                    raw = llm.call([{"role": "user", "content": prompt}])
                    start, end = raw.find("["), raw.rfind("]") + 1
                    if start != -1 and end > start:
                        try:
                            entities = json.loads(raw[start:end])
                            graph_store.store_entities(entities, orig_name, tier=tier, session_token=token)
                            step_log.append(f"[{orig_name}] Stored {len(entities)} entities in Neo4j.")
                        except json.JSONDecodeError as je:
                            step_log.append(f"[{orig_name}] Failed to parse JSON entities: {je}")
                            log.warning("JSON decode error during entity extraction for %s: %s", orig_name, je)
                    else:
                        step_log.append(f"[{orig_name}] No JSON array boundaries found in LLM output (skipped graph step).")
                else:
                    step_log.append(f"[{orig_name}] Neo4j offline — graph extraction skipped.")

                _jobs[job_id]["results"].append({
                    "file": orig_name, "ok": True,
                    "result": f"Ingested {added} chunks",
                    "log": step_log,
                })
            except Exception as exc:
                step_log.append(f"[{orig_name}] ERROR: {exc}")
                log.exception("Job %s: ingestion failed for %s", job_id, orig_name)
                _jobs[job_id]["results"].append({
                    "file": orig_name, "ok": False,
                    "result": str(exc), "log": step_log,
                })
            finally:
                if os.path.exists(path):
                    try:
                        os.remove(path)
                        log.info("Deleted local upload file: %s", path)
                    except OSError as e:
                        log.warning("Failed to delete %s: %s", path, e)
                        
            _jobs[job_id]["log"].extend(step_log)
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
    """RAG query — returns a streaming SSE response."""
    data = request.get_json()
    q    = (data or {}).get("query", "").strip()
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
                    q_events.put({"status": status})
                ans, metrics = run_query_crew(q, session_token=token, status_callback=cb)
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


# ── Headless API v1 ───────────────────────────────────────────────────────────

@app.route("/api/v1/query", methods=["POST"])
def query_v1():
    """Headless RAG query — synchronous JSON response."""
    data = request.get_json()
    q    = (data or {}).get("query", "").strip()
    if not q:
        return jsonify({"error": "Empty query"}), 400

    token = get_session_token()
    chunk_count = vector_store.count()
    if chunk_count == 0:
        return jsonify({"error": "No documents ingested yet."}), 400

    log.info("v1 Query received (%d chars) | session: %s", len(q), token)
    config.current_session.set(token)
    try:
        ans, metrics = run_query_crew(q, session_token=token)
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
        safe_name = f"{uuid.uuid4().hex[:6]}_{Path(f.filename).name}"
        dest = os.path.join(config.UPLOAD_FOLDER, safe_name)
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
            
            entities_stored = 0
            if graph_store.is_available():
                sample_text = "\n\n".join(d["text"] for d in docs[:3])[:3000]
                from agents.llm import get_llm
                llm = get_llm()
                prompt = (
                    "Extract key entities from the text below.\n"
                    "Return a JSON array of objects with keys: name, type, relations.\n"
                    "type must be a broad category like: Person, Organization, Location, Concept, Event, Document, Object, Rule.\n"
                    "relations is a list of {target, rel} objects.\n"
                    "Return ONLY the JSON array.\n\n"
                    f"TEXT:\n{sample_text}\n\nJSON:"
                )
                raw = llm.call([{"role": "user", "content": prompt}])
                start, end = raw.find("["), raw.rfind("]") + 1
                if start != -1 and end > start:
                    try:
                        entities = json.loads(raw[start:end])
                        graph_store.store_entities(entities, orig_name, tier=tier, session_token=token)
                        entities_stored = len(entities)
                    except Exception:
                        pass
            
            results.append({
                "file": orig_name, 
                "status": "success", 
                "chunks_added": added,
                "entities_added": entities_stored
            })
        except Exception as e:
            results.append({"file": orig_name, "status": "error", "error": str(e)})
        finally:
            if os.path.exists(path):
                os.remove(path)
                
    trigger_kv_cache_update(token)
    return jsonify({"results": results, "rejected": rejected})

# ── LLM probe endpoints (used by default prompt buttons) ─────────────────────

@app.route("/api/probe/gen", methods=["POST"])
def probe_gen():
    """Quick smoke-test for the gen_llm server."""
    import requests as req
    try:
        r = req.post(
            config.LLM_COMPLETIONS_URL,
            json={"prompt": "Hello, reply with one sentence.", "max_tokens": 64,
                  "temperature": 0.7, "top_p": 0.9},
            timeout=30,
            verify=False,
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
            timeout=30,
            verify=False,
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


if __name__ == "__main__":
    print("=" * 60)
    print("  Document AI Expert")
    print(f"  Gen LLM:   {config.LLM_COMPLETIONS_URL}  [{config.LLM_MODEL_ID}]")
    print(f"  Embed LLM: {config.EMBED_EMBEDDINGS_URL}  [{config.EMBEDDING_MODEL}]")
    print(f"  ChromaDB:  {config.CHROMA_PERSIST_DIR}  (embedded)")
    print(f"  Neo4j:     {config.NEO4J_URI}")
    print("=" * 60)
    
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    cert_path = str(Path(__file__).parent / "cert.pem")
    key_path = str(Path(__file__).parent / "key.pem")
    
    if os.path.exists(cert_path) and os.path.exists(key_path) and not os.environ.get("SPACE_ID"):
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)), debug=False, threaded=True, ssl_context=(cert_path, key_path))
    else:
        log.warning("SSL certificates not found or disabled (HF Space)! Running in HTTP mode.")
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)), debug=False, threaded=True)
