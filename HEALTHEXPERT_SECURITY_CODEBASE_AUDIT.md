Run started:2026-05-30 14:58:31.696372+00:00

Test results:
>> Issue: [B107:hardcoded_password_default] Possible hardcoded password: 'admin'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b107_hardcoded_password_default.html
   Location: ./agents/crew.py:146:0
145	
146	def run_query_crew(query: str, session_token: str = "admin", status_callback=None) -> tuple[str, dict]:
147	    """Run the hybrid retrieval + direct LLM synthesis pipeline.
148	
149	    OPTIMIZED PIPELINE (bypasses CrewAI for query path):
150	    -----------------------------------------------------
151	    Phase 1 — Retrieval (no LLM, instant):
152	        - vector_search: embed query → top-K cosine+BM25 chunks from ChromaDB
153	        - graph_search:  query Neo4j for related entities (if available)
154	
155	    Phase 2 — Gatekeeping (zero LLM cost — pure Python):
156	        - If BOTH vector DB and graph DB returned nothing → terminate immediately.
157	        - No context is sent to an LLM. No prompt-injection risk at this stage.
158	
159	    Phase 3 — Synthesis (1 direct LLM call — no CrewAI overhead):
160	        - Calls LocalLLM.call() directly with a focused synthesis prompt.
161	        - Bypasses CrewAI ReAct loop (was 3 LLM calls: plan + tool + reflect).
162	
163	    Total: 1 LLM call per query.
164	    """
165	    start_time = time.time()
166	
167	    total_prompt_tokens     = 0
168	    total_completion_tokens = 0
169	
170	    llm = _get_llm()  # reuse module-level singleton — zero construction overhead
171	
172	    # ── Phase 1: Retrieval (no LLM — pure vector + graph search) ─────────────
173	    if status_callback:
174	        status_callback("inference")
175	
176	    print(f"\n[Retrieval Phase] Performing vector+graph search for: '{query}'")
177	    t0 = time.time()
178	
179	    # Import pipeline modules directly for fast retrieval (bypasses CrewAI overhead)
180	    from pipeline import embedder, vector_store, graph_store
181	    import config as cfg
182	
183	    # Dense+BM25 hybrid vector search
184	    try:
185	        q_emb = embedder.embed_query(query)
186	        vec_results = vector_store.query(
187	            q_emb,
188	            top_k=cfg.TOP_K_VECTOR,
189	            keyword=query,
190	            session_token=session_token,
191	        )
192	        vec_ctx_parts = []
193	        for i, r in enumerate(vec_results, 1):
194	            src   = r["metadata"].get("source", "unknown")
195	            score = r["score"]
196	            vec_ctx_parts.append(f"[{i}] (source: {src}, relevance: {score:.2f})\n{r['text']}")
197	        vec_context = "\n\n---\n\n".join(vec_ctx_parts) if vec_ctx_parts else ""
198	    except Exception as e:
199	        print(f"[Retrieval] Vector search failed: {e}")
200	        vec_context = ""
201	
202	    # Graph context (non-blocking, skipped if Neo4j is offline)
203	    graph_context = ""
204	    try:
205	        if graph_store.is_available():
206	            # Use query words as entity hints
207	            entity_hints = [w for w in query.split() if len(w) > 4][:5]
208	            related = graph_store.query_related(entity_hints, hops=2, session_token=session_token)
209	            if related:
210	                graph_context = "Related entities from knowledge graph:\n" + "\n".join(f"- {r}" for r in related)
211	    except Exception as e:
212	        print(f"[Retrieval] Graph search failed (non-fatal): {e}")
213	
214	    # Combine contexts
215	    context_parts = []
216	    if vec_context:
217	        context_parts.append(f"--- Vector DB Results ---\n{vec_context}")
218	    if graph_context:
219	        context_parts.append(f"--- Knowledge Graph ---\n{graph_context}")
220	    context_output = "\n\n".join(context_parts) if context_parts else "No relevant documents found."
221	
222	    t_retrieval = time.time() - t0
223	    print(f"[Retrieval Phase] Done in {t_retrieval:.2f}s — "
224	          f"{len(vec_results) if vec_context else 0} vector chunks, "
225	          f"{'graph available' if graph_context else 'no graph context'}")
226	
227	    # ── Phase 2: Gatekeeping (zero LLM cost — pure Python) ───────────────────
228	    # Since we use vector RAG (not full KB injection), there is no prompt-injection
229	    # risk at this stage — the gate purely checks whether retrieval returned anything.
230	    # If both vector DB and graph DB are empty → terminate without any LLM call.
231	    if status_callback:
232	        status_callback("gatekeeping")
233	
234	    retrieval_is_empty = (not vec_context) and (not graph_context)
235	
236	    print(f"[Gatekeeper] vec_results={bool(vec_context)}, graph_results={bool(graph_context)}, "
237	          f"empty={retrieval_is_empty}")
238	
239	    if retrieval_is_empty:
240	        end_time = time.time()
241	        metrics = {
242	            "tokens_in":    total_prompt_tokens,
243	            "tokens_out":   total_completion_tokens,
244	            "time_seconds": end_time - start_time,
245	            "carbon_kg":    ((total_prompt_tokens + total_completion_tokens) / 1000) * 0.0003,
246	        }
247	        return "Internal data does not have any information to answer the question.", metrics
248	
249	
250	    # ── Phase 3: Synthesis (1 direct LLM call — no CrewAI overhead) ──────────
251	    # Direct call bypasses CrewAI's ReAct loop which was making 3 LLM round-trips:
252	    #   (1) plan which tool to use, (2) call synthesize_answer tool, (3) reflect on output.
253	    # Now it's a single model.generate() call on the GPU.
254	    if status_callback:
255	        status_callback("analysis")
256	
257	    synthesis_prompt = (
258	        "You are an expert Information Analyst. "
259	        "Answer the question using ONLY the provided context. "
260	        "Do not use any external knowledge or make assumptions beyond what is in the context.\n\n"
261	        f"CONTEXT:\n{context_output}\n\n"
262	        f"QUESTION: {query}\n\n"
263	        "FORMAT: Respond in clear Markdown. Use bullet points for key facts. "
264	        "Include source citations as [Source: filename]. "
265	        "End with a concise Summary section.\n\n"
266	        "ANSWER:"
267	    )
268	
269	    answer_text = llm.call([{"role": "user", "content": synthesis_prompt}])
270	
271	    # Track token usage from LocalLLM's last call (stored internally)
272	    total_prompt_tokens     += getattr(llm, "_last_prompt_tokens",     0)
273	    total_completion_tokens += getattr(llm, "_last_completion_tokens", 0)
274	
275	    end_time     = time.time()
276	    total_tokens = total_prompt_tokens + total_completion_tokens
277	    carbon_kg    = (total_tokens / 1000) * 0.0003
278	
279	    metrics = {
280	        "tokens_in":    total_prompt_tokens,
281	        "tokens_out":   total_completion_tokens,
282	        "time_seconds": end_time - start_time,
283	        "carbon_kg":    carbon_kg,
284	    }
285	
286	    # Strip any <think>...</think> block Qwen3 may emit
287	    if "<think>" in answer_text:
288	        think_end = answer_text.find("</think>")
289	        if think_end != -1:
290	            answer_text = answer_text[think_end + len("</think>"):].strip()
291	
292	    return answer_text, metrics

--------------------------------------------------
>> Issue: [B615:huggingface_unsafe_download] Unsafe Hugging Face Hub download without revision pinning in from_pretrained()
   Severity: Medium   Confidence: High
   CWE: CWE-494 (https://cwe.mitre.org/data/definitions/494.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b615_huggingface_unsafe_download.html
   Location: ./agents/gen_llm.py:96:16
95	try:
96	    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True)
97	

--------------------------------------------------
>> Issue: [B615:huggingface_unsafe_download] Unsafe Hugging Face Hub download without revision pinning in from_pretrained()
   Severity: Medium   Confidence: High
   CWE: CWE-494 (https://cwe.mitre.org/data/definitions/494.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b615_huggingface_unsafe_download.html
   Location: ./agents/gen_llm.py:107:12
106	
107	    model = AutoModelForCausalLM.from_pretrained(MODEL_NAME, **load_kwargs)
108	    model.eval()

--------------------------------------------------
>> Issue: [B110:try_except_pass] Try, Except, Pass detected.
   Severity: Low   Confidence: High
   CWE: CWE-703 (https://cwe.mitre.org/data/definitions/703.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b110_try_except_pass.html
   Location: ./agents/gen_llm.py:177:8
176	            vram_free_gib = round(torch.cuda.mem_get_info(_cuda_idx)[0] / 1024 ** 3, 2)
177	        except Exception:
178	            pass
179	    return jsonify({

--------------------------------------------------
>> Issue: [B501:request_with_no_cert_validation] Call to requests with verify=False disabling SSL certificate checks, security issue.
   Severity: High   Confidence: High
   CWE: CWE-295 (https://cwe.mitre.org/data/definitions/295.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b501_request_with_no_cert_validation.html
   Location: ./agents/llm.py:43:19
42	                timeout=self.timeout,
43	                verify=False,
44	            )
45	            resp.raise_for_status()
46	            data = resp.json()
47	            usage = data.get("usage", {})
48	            # Store token counts so callers can read them without CrewAI usage_metrics
49	            self._last_prompt_tokens     = usage.get("prompt_tokens",     0)

--------------------------------------------------
>> Issue: [B404:blacklist] Consider possible security implications associated with the subprocess module.
   Severity: Low   Confidence: High
   CWE: CWE-78 (https://cwe.mitre.org/data/definitions/78.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/blacklists/blacklist_imports.html#b404-import-subprocess
   Location: ./app.py:7:0
6	os.environ["PYTHONWARNINGS"] = "ignore"
7	import uuid, json, threading, subprocess, logging, warnings, signal, time
8	from pathlib import Path

--------------------------------------------------
>> Issue: [B105:hardcoded_password_string] Possible hardcoded password: 'anonymous'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b105_hardcoded_password_string.html
   Location: ./app.py:58:16
57	    if not token:
58	        token = "anonymous"
59	    _active_sessions[token] = time.time()

--------------------------------------------------
>> Issue: [B107:hardcoded_password_default] Possible hardcoded password: 'admin'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b107_hardcoded_password_default.html
   Location: ./app.py:62:0
61	
62	def trigger_kv_cache_update(session_token: str = "admin"):
63	    """Fetches all text and sends it to gen_llm to update KV cache."""
64	    def _update(token):
65	        from pipeline import vector_store
66	        import requests
67	        text = vector_store.get_all_text(session_token=token)
68	        log.info("Triggering KV cache update with %d chars...", len(text))
69	        try:
70	            requests.post(f"{config.LLM_BASE_URL}/v1/kv_cache", json={"text": text}, timeout=120, verify=False)
71	            log.info("KV Cache updated successfully.")
72	        except Exception as e:
73	            log.error("Failed to update KV Cache: %s", e)
74	    threading.Thread(target=_update, args=(session_token,), daemon=True).start()
75	

--------------------------------------------------
>> Issue: [B501:request_with_no_cert_validation] Call to requests with verify=False disabling SSL certificate checks, security issue.
   Severity: High   Confidence: High
   CWE: CWE-295 (https://cwe.mitre.org/data/definitions/295.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b501_request_with_no_cert_validation.html
   Location: ./app.py:70:12
69	        try:
70	            requests.post(f"{config.LLM_BASE_URL}/v1/kv_cache", json={"text": text}, timeout=120, verify=False)
71	            log.info("KV Cache updated successfully.")

--------------------------------------------------
>> Issue: [B603:subprocess_without_shell_equals_true] subprocess call - check for execution of untrusted input.
   Severity: Low   Confidence: High
   CWE: CWE-78 (https://cwe.mitre.org/data/definitions/78.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b603_subprocess_without_shell_equals_true.html
   Location: ./app.py:89:17
88	    try:
89	        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
90	        ok  = result.returncode == 0

--------------------------------------------------
>> Issue: [B105:hardcoded_password_string] Possible hardcoded password: 'admin'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b105_hardcoded_password_string.html
   Location: ./app.py:122:31
121	        expired = [token for token, last_active in _active_sessions.items() 
122	                   if token != "admin" and token != "anonymous" and (now - last_active) > SESSION_TIMEOUT_SECONDS]
123	        for token in expired:

--------------------------------------------------
>> Issue: [B105:hardcoded_password_string] Possible hardcoded password: 'anonymous'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b105_hardcoded_password_string.html
   Location: ./app.py:122:52
121	        expired = [token for token, last_active in _active_sessions.items() 
122	                   if token != "admin" and token != "anonymous" and (now - last_active) > SESSION_TIMEOUT_SECONDS]
123	        for token in expired:

--------------------------------------------------
>> Issue: [B501:request_with_no_cert_validation] Call to requests with verify=False disabling SSL certificate checks, security issue.
   Severity: High   Confidence: High
   CWE: CWE-295 (https://cwe.mitre.org/data/definitions/295.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b501_request_with_no_cert_validation.html
   Location: ./app.py:151:12
150	    try:
151	        r = req.get(f"{config.LLM_BASE_URL}/health", timeout=3, verify=False)
152	        gen_ok = r.status_code == 200

--------------------------------------------------
>> Issue: [B501:request_with_no_cert_validation] Call to requests with verify=False disabling SSL certificate checks, security issue.
   Severity: High   Confidence: High
   CWE: CWE-295 (https://cwe.mitre.org/data/definitions/295.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b501_request_with_no_cert_validation.html
   Location: ./app.py:163:12
162	    try:
163	        r = req.get(f"{config.EMBED_BASE_URL}/health", timeout=3, verify=False)
164	        embed_ok = r.status_code == 200

--------------------------------------------------
>> Issue: [B110:try_except_pass] Try, Except, Pass detected.
   Severity: Low   Confidence: High
   CWE: CWE-703 (https://cwe.mitre.org/data/definitions/703.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b110_try_except_pass.html
   Location: ./app.py:580:20
579	                        entities_stored = len(entities)
580	                    except Exception:
581	                        pass
582	            

--------------------------------------------------
>> Issue: [B501:request_with_no_cert_validation] Call to requests with verify=False disabling SSL certificate checks, security issue.
   Severity: High   Confidence: High
   CWE: CWE-295 (https://cwe.mitre.org/data/definitions/295.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b501_request_with_no_cert_validation.html
   Location: ./app.py:610:12
609	            timeout=30,
610	            verify=False,
611	        )
612	        r.raise_for_status()
613	        data = r.json()
614	        text = data["choices"][0]["text"].strip()
615	        return jsonify({"ok": True, "model": data.get("model"), "response": text})
616	    except Exception as exc:
617	        log.error("probe_gen failed: %s", exc)

--------------------------------------------------
>> Issue: [B501:request_with_no_cert_validation] Call to requests with verify=False disabling SSL certificate checks, security issue.
   Severity: High   Confidence: High
   CWE: CWE-295 (https://cwe.mitre.org/data/definitions/295.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b501_request_with_no_cert_validation.html
   Location: ./app.py:630:12
629	            timeout=30,
630	            verify=False,
631	        )
632	        r.raise_for_status()
633	        data = r.json()
634	        vec  = data["data"][0]["embedding"]
635	        return jsonify({
636	            "ok": True,

--------------------------------------------------
>> Issue: [B104:hardcoded_bind_all_interfaces] Possible binding to all interfaces.
   Severity: Medium   Confidence: Medium
   CWE: CWE-605 (https://cwe.mitre.org/data/definitions/605.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b104_hardcoded_bind_all_interfaces.html
   Location: ./app.py:662:21
661	    if os.path.exists(cert_path) and os.path.exists(key_path):
662	        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)), debug=False, threaded=True, ssl_context=(cert_path, key_path))
663	    else:

--------------------------------------------------
>> Issue: [B104:hardcoded_bind_all_interfaces] Possible binding to all interfaces.
   Severity: Medium   Confidence: Medium
   CWE: CWE-605 (https://cwe.mitre.org/data/definitions/605.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b104_hardcoded_bind_all_interfaces.html
   Location: ./app.py:665:21
664	        log.warning("SSL certificates not found! Running in HTTP mode.")
665	        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5050)), debug=False, threaded=True)

--------------------------------------------------
>> Issue: [B404:blacklist] Consider possible security implications associated with the subprocess module.
   Severity: Low   Confidence: High
   CWE: CWE-78 (https://cwe.mitre.org/data/definitions/78.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/blacklists/blacklist_imports.html#b404-import-subprocess
   Location: ./manage_db.py:25:0
24	import argparse
25	import subprocess
26	import sys

--------------------------------------------------
>> Issue: [B603:subprocess_without_shell_equals_true] subprocess call - check for execution of untrusted input.
   Severity: Low   Confidence: High
   CWE: CWE-78 (https://cwe.mitre.org/data/definitions/78.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b603_subprocess_without_shell_equals_true.html
   Location: ./manage_db.py:38:8
37	    try:
38	        subprocess.run(cmd, check=True)
39	    except subprocess.CalledProcessError as e:

--------------------------------------------------
>> Issue: [B501:request_with_no_cert_validation] Call to requests with verify=False disabling SSL certificate checks, security issue.
   Severity: High   Confidence: High
   CWE: CWE-295 (https://cwe.mitre.org/data/definitions/295.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b501_request_with_no_cert_validation.html
   Location: ./pipeline/embedder.py:31:15
30	            timeout = config.EMBEDDING_TIMEOUT,
31	            verify  = False,
32	        )
33	        resp.raise_for_status()
34	        data = resp.json()
35	        # data["data"] is a list of {"index": i, "embedding": [...]}
36	        # Sort by index to preserve input order
37	        items = sorted(data["data"], key=lambda d: d["index"])

--------------------------------------------------
>> Issue: [B501:request_with_no_cert_validation] Call to requests with verify=False disabling SSL certificate checks, security issue.
   Severity: High   Confidence: High
   CWE: CWE-295 (https://cwe.mitre.org/data/definitions/295.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b501_request_with_no_cert_validation.html
   Location: ./pipeline/embedder.py:75:15
74	            timeout = config.EMBEDDING_TIMEOUT,
75	            verify  = False,
76	        )
77	        resp.raise_for_status()
78	        return resp.json()
79	    except requests.exceptions.ConnectionError:
80	        log.error("[Embedder] Cannot connect to embed_llm server at %s — is it running?",
81	                  config.EMBED_BASE_URL)

--------------------------------------------------
>> Issue: [B107:hardcoded_password_default] Possible hardcoded password: 'admin'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b107_hardcoded_password_default.html
   Location: ./pipeline/graph_store.py:60:0
59	
60	def store_entities(entities: list[dict], source: str, tier: str = "extended", session_token: str = "admin") -> None:
61	    """
62	    entities: [{"name": str, "type": str, "relations": [{"target": str, "rel": str}]}]
63	    """
64	    d = _get_driver()
65	    if not d:
66	        return
67	    with d.session() as s:
68	        for ent in entities:
69	            if not isinstance(ent, dict):
70	                continue
71	            ent_name = ent.get("name") or ent.get("entity") or ent.get("id")
72	            if not ent_name:
73	                continue
74	            s.run(
75	                """
76	                MERGE (e:Entity {name: $name})
77	                SET e.type = $type, e.source = $source, e.tier = $tier, e.session_token = $session_token
78	                """,
79	                name=ent_name, type=ent.get("type", "General"), source=source, tier=tier, session_token=session_token
80	            )
81	            for rel in ent.get("relations", []):
82	                if not isinstance(rel, dict):
83	                    continue
84	                tgt = rel.get("target") or rel.get("to")
85	                if not tgt:
86	                    continue
87	                s.run(
88	                    """
89	                    MERGE (a:Entity {name: $src})
90	                    MERGE (b:Entity {name: $tgt})
91	                    MERGE (a)-[r:RELATES_TO {type: $rel}]->(b)
92	                    SET r.source = $source, r.tier = $tier, r.session_token = $session_token
93	                    """,
94	                    src=ent_name, tgt=tgt,
95	                    rel=rel.get("rel", "related_to"), source=source, tier=tier, session_token=session_token
96	                )
97	

--------------------------------------------------
>> Issue: [B107:hardcoded_password_default] Possible hardcoded password: 'admin'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b107_hardcoded_password_default.html
   Location: ./pipeline/graph_store.py:99:0
98	
99	def query_related(entity_names: list[str], hops: int = 2, session_token: str = "admin") -> list[str]:
100	    """Return text snippets of related entities within `hops` graph hops."""
101	    d = _get_driver()
102	    if not d:
103	        return []
104	    results = []
105	    with d.session() as s:
106	        for name in entity_names:
107	            records = s.run(
108	                f"""
109	                MATCH (e:Entity {{name: $name}})
110	                WHERE e.tier = 'foundation' OR e.session_token = $session_token OR $session_token = 'admin'
111	                OPTIONAL MATCH (e)-[r:RELATES_TO*1..{hops}]-(related)
112	                WHERE related IS NOT NULL AND (related.tier = 'foundation' OR related.session_token = $session_token OR $session_token = 'admin')
113	                RETURN DISTINCT related.name AS name, related.type AS type
114	                LIMIT $limit
115	                """,
116	                name=name, limit=config.TOP_K_GRAPH * 3, session_token=session_token
117	            ).data()
118	            for rec in records:
119	                results.append(f"{rec['name']} ({rec['type']})")
120	    return results[:config.TOP_K_GRAPH * 3]
121	

--------------------------------------------------
>> Issue: [B107:hardcoded_password_default] Possible hardcoded password: 'admin'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b107_hardcoded_password_default.html
   Location: ./pipeline/graph_store.py:123:0
122	
123	def delete_source(source: str, session_token: str = "admin") -> None:
124	    d = _get_driver()
125	    if not d:
126	        return
127	    with d.session() as s:
128	        if session_token == "admin":
129	            s.run("MATCH (e:Entity {source: $source}) DETACH DELETE e", source=source)
130	        else:
131	            s.run("MATCH (e:Entity {source: $source, session_token: $session_token}) DETACH DELETE e", 
132	                  source=source, session_token=session_token)
133	

--------------------------------------------------
>> Issue: [B105:hardcoded_password_string] Possible hardcoded password: 'admin'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b105_hardcoded_password_string.html
   Location: ./pipeline/graph_store.py:128:28
127	    with d.session() as s:
128	        if session_token == "admin":
129	            s.run("MATCH (e:Entity {source: $source}) DETACH DELETE e", source=source)

--------------------------------------------------
>> Issue: [B107:hardcoded_password_default] Possible hardcoded password: 'admin'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b107_hardcoded_password_default.html
   Location: ./pipeline/vector_store.py:129:0
128	
129	def add_chunks(
130	    chunks: list[dict],
131	    embeddings: list[list[float]],
132	    doc_id: str,
133	    tier: str = "extended",
134	    session_token: str = "admin",
135	) -> int:
136	    """Store chunks with their embeddings and knowledge tier. Returns number of items added."""
137	    col = _get_collection()
138	
139	    ids, docs, metadatas, vecs = [], [], [], []
140	    for i, (chunk, vector) in enumerate(zip(chunks, embeddings)):
141	        text = chunk.get("text", "")
142	        enc_text = encrypt_data(text)
143	        chunk_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}_{i}"))
144	
145	        ids.append(chunk_id)
146	        docs.append(enc_text)           # stored document = encrypted text
147	        metadatas.append({
148	            "source":        chunk.get("source", "unknown"),
149	            "file_type":     chunk.get("file_type", "?"),
150	            "tier":          tier,
151	            "session_token": session_token,
152	        })
153	        vecs.append(vector)
154	
155	    # ChromaDB batch upsert
156	    col.upsert(ids=ids, documents=docs, metadatas=metadatas, embeddings=vecs)
157	
158	    # Invalidate caches so next read reflects the new data
159	    global _text_cache_valid, _bm25_cache
160	    _text_cache_valid = False
161	    _bm25_cache = None
162	
163	    return len(chunks)
164	

--------------------------------------------------
>> Issue: [B107:hardcoded_password_default] Possible hardcoded password: 'admin'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b107_hardcoded_password_default.html
   Location: ./pipeline/vector_store.py:166:0
165	
166	def query(
167	    query_embedding: list[float],
168	    top_k: int | None = None,
169	    keyword: str | None = None,
170	    session_token: str = "admin",
171	) -> list[dict]:
172	    """Return top_k most similar chunks using DB25 hybrid search.
173	
174	    DB25 = Dense (ChromaDB cosine ANN) + BM25, fused via RRF.
175	    Falls back to pure dense search when keyword is None.
176	    """
177	    k = top_k or config.TOP_K_VECTOR
178	    col = _get_collection()
179	
180	    # RBAC where-clause: foundation docs are globally readable; session docs only by owner/admin
181	    if session_token == "admin":
182	        where_filter = None  # admin sees everything
183	    else:
184	        where_filter = {
185	            "$or": [
186	                {"tier": {"$eq": "foundation"}},
187	                {"session_token": {"$eq": session_token}},
188	            ]
189	        }
190	
191	    # Oversample for BM25 re-ranking (4× oversample, min 20)
192	    fetch_k = max(k * 4, 20) if keyword else k
193	
194	    query_kwargs: dict = dict(
195	        query_embeddings=[query_embedding],
196	        n_results=min(fetch_k, max(col.count(), 1)),
197	        include=["documents", "metadatas", "distances"],
198	    )
199	    if where_filter:
200	        query_kwargs["where"] = where_filter
201	
202	    raw = col.query(**query_kwargs)
203	
204	    # Decrypt texts for BM25 and output
205	    enc_texts = raw["documents"][0] if raw["documents"] else []
206	    plain_texts = [decrypt_data(enc) for enc in enc_texts]
207	
208	    if keyword and plain_texts:
209	        # DB25: dense + BM25 fusion
210	        return _db25_fuse(raw, plain_texts, keyword, top_k=k)
211	
212	    # Pure dense fallback (no keyword supplied)
213	    results = []
214	    ids = raw["ids"][0] if raw["ids"] else []
215	    metadatas = raw["metadatas"][0] if raw["metadatas"] else []
216	    distances = raw["distances"][0] if raw["distances"] else []
217	    for text, meta, dist in zip(plain_texts, metadatas, distances):
218	        results.append({
219	            "text": text,
220	            "metadata": {
221	                "source": meta.get("source"),
222	                "file_type": meta.get("file_type"),
223	                "tier": meta.get("tier"),
224	            },
225	            "score": max(0.0, 1.0 - dist),
226	        })
227	    return results[:k]
228	

--------------------------------------------------
>> Issue: [B105:hardcoded_password_string] Possible hardcoded password: 'admin'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b105_hardcoded_password_string.html
   Location: ./pipeline/vector_store.py:181:24
180	    # RBAC where-clause: foundation docs are globally readable; session docs only by owner/admin
181	    if session_token == "admin":
182	        where_filter = None  # admin sees everything

--------------------------------------------------
>> Issue: [B107:hardcoded_password_default] Possible hardcoded password: 'admin'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b107_hardcoded_password_default.html
   Location: ./pipeline/vector_store.py:230:0
229	
230	def list_documents(session_token: str = "admin") -> list[dict]:
231	    """Return unique source documents stored in the collection."""
232	    col = _get_collection()
233	
234	    # Fetch all metadata (no embeddings needed)
235	    all_meta = col.get(include=["metadatas"])["metadatas"] or []
236	
237	    seen, docs = set(), []
238	    for meta in all_meta:
239	        tier = meta.get("tier", "extended")
240	        tok  = meta.get("session_token", "")
241	        if session_token != "admin" and tier != "foundation" and tok != session_token:
242	            continue
243	        src = meta.get("source", "unknown")
244	        if src not in seen:
245	            seen.add(src)
246	            docs.append({
247	                "source":    src,
248	                "file_type": meta.get("file_type", "?"),
249	                "tier":      tier,
250	            })
251	    return docs
252	

--------------------------------------------------
>> Issue: [B105:hardcoded_password_string] Possible hardcoded password: 'admin'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b105_hardcoded_password_string.html
   Location: ./pipeline/vector_store.py:241:28
240	        tok  = meta.get("session_token", "")
241	        if session_token != "admin" and tier != "foundation" and tok != session_token:
242	            continue

--------------------------------------------------
>> Issue: [B107:hardcoded_password_default] Possible hardcoded password: 'admin'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b107_hardcoded_password_default.html
   Location: ./pipeline/vector_store.py:254:0
253	
254	def get_all_text(session_token: str = "admin") -> str:
255	    """Return all document text in the knowledge base, concatenated.
256	
257	    Performance: caches the admin result and returns it immediately on
258	    subsequent calls until cache is invalidated by add/delete/purge.
259	    """
260	    global _text_cache, _text_cache_valid
261	
262	    # Fast path: return cached result for admin (most common caller)
263	    if session_token == "admin" and _text_cache_valid and _text_cache is not None:
264	        return _text_cache
265	
266	    col = _get_collection()
267	
268	    all_data = col.get(include=["documents", "metadatas"])
269	    enc_docs  = all_data.get("documents") or []
270	    metadatas = all_data.get("metadatas") or []
271	
272	    texts = []
273	    for enc_text, meta in zip(enc_docs, metadatas):
274	        tier = meta.get("tier", "extended")
275	        tok  = meta.get("session_token", "")
276	        if session_token != "admin" and tier != "foundation" and tok != session_token:
277	            continue
278	        text = decrypt_data(enc_text)
279	        if text:
280	            texts.append(text)
281	
282	    result = "\n\n".join(texts)
283	
284	    # Populate cache for admin queries
285	    if session_token == "admin":
286	        _text_cache = result
287	        _text_cache_valid = True
288	
289	    return result
290	

--------------------------------------------------
>> Issue: [B105:hardcoded_password_string] Possible hardcoded password: 'admin'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b105_hardcoded_password_string.html
   Location: ./pipeline/vector_store.py:263:24
262	    # Fast path: return cached result for admin (most common caller)
263	    if session_token == "admin" and _text_cache_valid and _text_cache is not None:
264	        return _text_cache

--------------------------------------------------
>> Issue: [B105:hardcoded_password_string] Possible hardcoded password: 'admin'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b105_hardcoded_password_string.html
   Location: ./pipeline/vector_store.py:276:28
275	        tok  = meta.get("session_token", "")
276	        if session_token != "admin" and tier != "foundation" and tok != session_token:
277	            continue

--------------------------------------------------
>> Issue: [B105:hardcoded_password_string] Possible hardcoded password: 'admin'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b105_hardcoded_password_string.html
   Location: ./pipeline/vector_store.py:285:24
284	    # Populate cache for admin queries
285	    if session_token == "admin":
286	        _text_cache = result

--------------------------------------------------
>> Issue: [B107:hardcoded_password_default] Possible hardcoded password: 'admin'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b107_hardcoded_password_default.html
   Location: ./pipeline/vector_store.py:292:0
291	
292	def delete_document(source_name: str, session_token: str = "admin") -> int:
293	    """Delete all chunks belonging to a source document. Returns deleted count."""
294	    col = _get_collection()
295	
296	    if session_token == "admin":
297	        where_filter = {"source": {"$eq": source_name}}
298	    else:
299	        where_filter = {
300	            "$and": [
301	                {"source": {"$eq": source_name}},
302	                {"session_token": {"$eq": session_token}},
303	            ]
304	        }
305	
306	    # Get IDs matching filter then delete
307	    result = col.get(where=where_filter, include=[])
308	    ids = result.get("ids") or []
309	    if ids:
310	        col.delete(ids=ids)
311	
312	    # Invalidate caches
313	    global _text_cache_valid, _bm25_cache
314	    _text_cache_valid = False
315	    _bm25_cache = None
316	
317	    return len(ids)
318	

--------------------------------------------------
>> Issue: [B105:hardcoded_password_string] Possible hardcoded password: 'admin'
   Severity: Low   Confidence: Medium
   CWE: CWE-259 (https://cwe.mitre.org/data/definitions/259.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b105_hardcoded_password_string.html
   Location: ./pipeline/vector_store.py:296:24
295	
296	    if session_token == "admin":
297	        where_filter = {"source": {"$eq": source_name}}

--------------------------------------------------
>> Issue: [B110:try_except_pass] Try, Except, Pass detected.
   Severity: Low   Confidence: High
   CWE: CWE-703 (https://cwe.mitre.org/data/definitions/703.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b110_try_except_pass.html
   Location: ./pipeline/vector_store.py:346:8
345	            _client.delete_collection(config.CHROMA_COLLECTION)
346	        except Exception:
347	            pass
348	    _client = None

--------------------------------------------------
>> Issue: [B404:blacklist] Consider possible security implications associated with the subprocess module.
   Severity: Low   Confidence: High
   CWE: CWE-78 (https://cwe.mitre.org/data/definitions/78.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/blacklists/blacklist_imports.html#b404-import-subprocess
   Location: ./scripts/generate_certs.py:2:0
1	import os
2	import subprocess
3	from pathlib import Path

--------------------------------------------------
>> Issue: [B607:start_process_with_partial_path] Starting a process with a partial executable path
   Severity: Low   Confidence: High
   CWE: CWE-78 (https://cwe.mitre.org/data/definitions/78.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b607_start_process_with_partial_path.html
   Location: ./scripts/generate_certs.py:13:12
12	        try:
13	            subprocess.run([
14	                "openssl", "req", "-x509", "-newkey", "rsa:4096",
15	                "-keyout", str(key_path), "-out", str(cert_path),
16	                "-days", "365", "-nodes", "-subj", "/CN=localhost"
17	            ], check=True)
18	            print("SSL certificates generated successfully.")

--------------------------------------------------
>> Issue: [B603:subprocess_without_shell_equals_true] subprocess call - check for execution of untrusted input.
   Severity: Low   Confidence: High
   CWE: CWE-78 (https://cwe.mitre.org/data/definitions/78.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b603_subprocess_without_shell_equals_true.html
   Location: ./scripts/generate_certs.py:13:12
12	        try:
13	            subprocess.run([
14	                "openssl", "req", "-x509", "-newkey", "rsa:4096",
15	                "-keyout", str(key_path), "-out", str(cert_path),
16	                "-days", "365", "-nodes", "-subj", "/CN=localhost"
17	            ], check=True)
18	            print("SSL certificates generated successfully.")

--------------------------------------------------
>> Issue: [B101:assert_used] Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.
   Severity: Low   Confidence: High
   CWE: CWE-703 (https://cwe.mitre.org/data/definitions/703.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b101_assert_used.html
   Location: ./test_healthexpert.py:8:4
7	    encrypted = encrypt_data(test_str)
8	    assert encrypted != test_str
9	    

--------------------------------------------------
>> Issue: [B101:assert_used] Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.
   Severity: Low   Confidence: High
   CWE: CWE-703 (https://cwe.mitre.org/data/definitions/703.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b101_assert_used.html
   Location: ./test_healthexpert.py:11:4
10	    decrypted = decrypt_data(encrypted)
11	    assert decrypted == test_str
12	

--------------------------------------------------
>> Issue: [B101:assert_used] Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.
   Severity: Low   Confidence: High
   CWE: CWE-703 (https://cwe.mitre.org/data/definitions/703.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b101_assert_used.html
   Location: ./test_healthexpert.py:14:4
13	def test_empty_encryption():
14	    assert encrypt_data("") == ""
15	    assert decrypt_data("") == ""

--------------------------------------------------
>> Issue: [B101:assert_used] Use of assert detected. The enclosed code will be removed when compiling to optimised byte code.
   Severity: Low   Confidence: High
   CWE: CWE-703 (https://cwe.mitre.org/data/definitions/703.html)
   More Info: https://bandit.readthedocs.io/en/1.9.4/plugins/b101_assert_used.html
   Location: ./test_healthexpert.py:15:4
14	    assert encrypt_data("") == ""
15	    assert decrypt_data("") == ""
16	

--------------------------------------------------

Code scanned:
	Total lines of code: 2270
	Total lines skipped (#nosec): 0
	Total potential issues skipped due to specifically being disabled (e.g., #nosec BXXX): 0

Run metrics:
	Total issues (by severity):
		Undefined: 0
		Low: 34
		Medium: 4
		High: 8
	Total issues (by confidence):
		Undefined: 0
		Low: 0
		Medium: 22
		High: 24
Files skipped (0):
