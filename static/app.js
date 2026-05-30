'use strict';

// ── Logging helper (browser console with timestamps) ───────────────────────
const LOG_PREFIX = '[HealthExpert]';
const diag = {
  info:  (...a) => console.info( `${LOG_PREFIX}`, ...a),
  warn:  (...a) => console.warn( `${LOG_PREFIX}`, ...a),
  error: (...a) => console.error(`${LOG_PREFIX}`, ...a),
  group: (label) => console.group(`${LOG_PREFIX} ${label}`),
  groupEnd: () => console.groupEnd(),
};

// ── State ──────────────────────────────────────────────────────────────────────
const state = {
  isQuerying:   false,
  isIngesting:  false,
  currentJobId: null,
  lastAnswer:   '',
  isAdmin:      false,
};

// ── DOM refs ───────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const dropZone        = $('drop-zone');
const fileInput       = $('file-input');
const docList         = $('doc-list');
const ingestProgress  = $('ingest-progress');
const progressFill    = $('progress-fill');
const ingestStatusTxt = $('ingest-status-text');
const ingestLog       = $('ingest-log');
const ingestLogDetails= $('ingest-log-details');
const chatHistory     = $('chat-history');
const queryInput      = $('query-input');
const sendBtn         = $('send-btn');
const outputContainer = $('output-container');
const copyBtn         = $('copy-btn');
const citationsBlock  = $('citations-block');
const notifContainer  = $('notif-container');
const dockerModal     = $('docker-modal');
const dockerModalTitle= $('docker-modal-title');
const dockerModalBody = $('docker-modal-body');

// ── Notifications ──────────────────────────────────────────────────────────────
function notify(msg, type = 'info', duration = 5000) {
  diag.info(`notify [${type}]`, msg);
  const el = document.createElement('div');
  el.className = `notif ${type}`;
  el.textContent = msg;
  notifContainer.appendChild(el);
  setTimeout(() => el.remove(), duration);
}

// ── Diagnostic log (ingestion accordion) ──────────────────────────────────────
function appendLog(msg) {
  const line = document.createElement('div');
  line.className = 'log-line';
  line.textContent = `${new Date().toLocaleTimeString()} ${msg}`;
  ingestLog.appendChild(line);
  ingestLog.scrollTop = ingestLog.scrollHeight;
}

function clearLog() {
  ingestLog.innerHTML = '';
}

// ── Status bar ─────────────────────────────────────────────────────────────────
async function refreshStatus() {
  diag.info('Refreshing status…');
  try {
    const r = await fetch('/api/status');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    diag.info('Status response:', d);

    const setPill = (id, ok, label) => {
      const pill = $(id);
      if (!pill) return;
      pill.className = `status-pill ${ok ? 'ok' : 'warn'}`;
      pill.querySelector('.status-label').textContent = label;
    };

    const vecOk   = (d.vector_db?.chunks ?? -1) >= 0;
    const graphOk = d.graph_db?.available;
    const genOk   = d.gen_llm?.online;
    const embedOk = d.embed_llm?.online;
    
    state.isAdmin = !!d.is_admin;
    const adminBadge = $('admin-badge');
    if (adminBadge) adminBadge.style.display = state.isAdmin ? 'inline' : 'none';
    const adminControls = $('admin-controls');
    if (adminControls) adminControls.style.display = state.isAdmin ? 'flex' : 'none';

    setPill('status-vector', vecOk,   `Vector · ${d.vector_db?.chunks ?? '?'} chunks`);
    setPill('status-graph',  graphOk, graphOk ? `Graph · ${d.graph_db.nodes} nodes, ${d.graph_db.relationships} edges` : 'Graph · offline');
    
    const genStatus = genOk 
        ? `Gen · ${(d.gen_llm?.model || '').split('/').pop()} (GPU: ${d.gen_llm?.gpu_id} | KV: ${d.gen_llm?.kv_cache_length} tkns)` 
        : 'Gen LLM · offline';
    setPill('status-gen',    genOk,   genStatus);
    
    setPill('status-embed',  embedOk, embedOk ? `Embed · ${(d.embed_llm?.model || '').split('/').pop()}` : 'Embed LLM · offline');

    if (!genOk)   diag.warn('gen_llm server is offline');
    if (!embedOk) diag.warn('embed_llm server is offline');
  } catch(err) {
    diag.error('Status refresh failed:', err);
    ['status-vector','status-graph','status-gen','status-embed'].forEach(id => {
      const p = $(id);
      if (p) p.className = 'status-pill err';
    });
  }
}

// ── Admin controls ────────────────────────────────────────────────────────────
async function dockerAction(action) {
  const labels = { up: '▶ Starting', down: '■ Stopping' };
  dockerModalTitle.textContent = `${labels[action] || action} Databases…`;
  dockerModalBody.textContent  = 'Running docker compose, please wait…';
  dockerModal.style.display    = 'flex';
  diag.info('Docker action:', action);

  try {
    const r    = await fetch(`/api/docker/${action}`, { method: 'POST' });
    const data = await r.json();
    diag.info('Docker response:', data);
    dockerModalBody.innerHTML =
      `<pre class="modal-pre">${escHtml(data.output || 'No output')}</pre>`;
    if (data.ok) {
      notify(`Database ${action} succeeded`, 'success');
      setTimeout(refreshStatus, 3000);
    } else {
      notify(`Database ${action} failed`, 'error', 8000);
    }
  } catch(err) {
    diag.error('Docker action failed:', err);
    dockerModalBody.textContent = `Error: ${err.message}`;
    notify(`Docker ${action} error: ${err.message}`, 'error', 8000);
  }
}

async function adminPurge() {
  if (!confirm("Are you sure you want to completely wipe Weaviate and Neo4j? This cannot be undone.")) return;
  diag.info('Purge DB action');
  try {
    const r = await fetch(`/api/admin/purge`, { method: 'POST' });
    const data = await r.json();
    if (data.ok) {
      notify(data.msg, 'success', 5000);
      await loadDocuments();
      await refreshStatus();
    } else {
      notify(`Purge failed: ${data.error}`, 'error', 8000);
    }
  } catch(err) {
    notify(`Purge error: ${err.message}`, 'error', 8000);
  }
}

async function adminKill() {
  if (!confirm("EMERGENCY KILL SWITCH: This will stop all databases and instantly kill the application server. Are you sure?")) return;
  diag.info('Kill switch activated');
  try {
    const r = await fetch(`/api/admin/kill`, { method: 'POST' });
    const data = await r.json();
    if (data.ok) {
      notify(data.msg, 'success', 10000);
      document.body.innerHTML = "<h1 style='color:red; text-align:center; margin-top:20%'>APPLICATION TERMINATED</h1><p style='text-align:center'>Please restart the server manually.</p>";
    } else {
      notify(`Kill switch failed: ${data.error}`, 'error', 8000);
    }
  } catch(err) {
    notify(`Kill switch error: ${err.message}`, 'error', 8000);
  }
}

$('db-up-btn').addEventListener('click',      () => dockerAction('up'));
$('db-down-btn').addEventListener('click',    () => dockerAction('down'));
$('db-purge-btn').addEventListener('click',   adminPurge);
$('app-kill-btn').addEventListener('click',   adminKill);
$('docker-modal-close').addEventListener('click', () => { dockerModal.style.display = 'none'; });

// ── Document list ──────────────────────────────────────────────────────────────
const TYPE_ICONS = {
  pdf:'📄', docx:'📝', txt:'📃', xlsx:'📊', csv:'📋',
  png:'🖼️', jpg:'🖼️', jpeg:'🖼️', webp:'🖼️',
};

async function loadDocuments() {
  diag.info('Loading document list…');
  try {
    const r    = await fetch('/api/documents');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    diag.info(`Document list: ${data.total} document(s)`);
    docList.innerHTML = '';

    if (!data.documents?.length) {
      docList.innerHTML = '<div class="empty-state">📂 No documents ingested yet.<br>Upload files above to get started.</div>';
      return;
    }

    data.documents.forEach(doc => {
      const icon = TYPE_ICONS[doc.file_type] || '📄';
      const item = document.createElement('div');
      item.className = 'doc-item';
      const tierBadge = doc.tier === 'foundation' 
          ? `<span style="font-size:10px; background:var(--blue); color:#fff; padding:2px 4px; border-radius:4px; margin-left:8px;">FOUNDATION</span>`
          : `<span style="font-size:10px; background:var(--bg-panel); border:1px solid var(--border); padding:2px 4px; border-radius:4px; margin-left:8px;">EXTENDED</span>`;
          
      item.innerHTML = `
        <button class="doc-delete" title="Remove document" data-source="${escHtml(doc.source)}" data-tier="${doc.tier}">✕</button>
        <span class="doc-icon">${icon}</span>
        <div class="doc-info">
          <div class="doc-name" title="${escHtml(doc.source)}">${escHtml(doc.source)} ${tierBadge}</div>
          <div class="doc-type">${doc.file_type.toUpperCase()}</div>
        </div>`;
      docList.appendChild(item);
    });

    docList.querySelectorAll('.doc-delete').forEach(btn => {
      btn.addEventListener('click', () => deleteDocument(btn.dataset.source, btn.dataset.tier));
    });
  } catch(err) {
    diag.error('loadDocuments failed:', err);
    notify(`Failed to load documents: ${err.message}`, 'error');
  }
}

async function deleteDocument(source, tier) {
  diag.info('Deleting document:', source, tier);
  try {
    const r    = await fetch(`/api/documents/${encodeURIComponent(source)}?tier=${tier}`, { method: 'DELETE' });
    const data = await r.json();
    diag.info('Delete response:', data);
    notify(`Removed "${source}" (${data.deleted_chunks} chunks)`, 'info');
    await loadDocuments();
    await refreshStatus();
  } catch(err) {
    diag.error('Delete failed:', err);
    notify(`Delete failed: ${err.message}`, 'error');
  }
}

$('refresh-docs-btn').addEventListener('click', loadDocuments);

// ── Drag & Drop & Tier Selection ───────────────────────────────────────────
let pendingFiles = [];
const tierModal = $('tier-modal');
const tierCancel = $('tier-modal-cancel');
const tierConfirm = $('tier-modal-confirm');
const tierFoundationLabel = $('tier-foundation-label');

function openTierModal(files) {
    if (!files.length) return;
    pendingFiles = files;
    if (!state.isAdmin) {
        if(tierFoundationLabel) {
            tierFoundationLabel.style.opacity = '0.5';
            tierFoundationLabel.querySelector('input').disabled = true;
        }
        document.querySelector('input[value="extended"]').checked = true;
    } else {
        if(tierFoundationLabel) {
            tierFoundationLabel.style.opacity = '1';
            tierFoundationLabel.querySelector('input').disabled = false;
        }
    }
    if (tierModal) tierModal.style.display = 'flex';
}

if (tierCancel) {
    tierCancel.addEventListener('click', () => {
        tierModal.style.display = 'none';
        pendingFiles = [];
    });
}
if (tierConfirm) {
    tierConfirm.addEventListener('click', () => {
        tierModal.style.display = 'none';
        const selectedTier = document.querySelector('input[name="kb_tier"]:checked').value;
        ingestFiles(pendingFiles, selectedTier);
    });
}

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('keydown', e => { if (e.key === 'Enter') fileInput.click(); });
dropZone.addEventListener('dragover',  e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  const files = Array.from(e.dataTransfer.files);
  diag.info(`Dropped ${files.length} file(s):`, files.map(f => f.name));
  openTierModal(files);
});
fileInput.addEventListener('change', () => {
  const files = Array.from(fileInput.files);
  diag.info(`Selected ${files.length} file(s):`, files.map(f => f.name));
  openTierModal(files);
});

// ── Ingestion ──────────────────────────────────────────────────────────────────
async function ingestFiles(files, tier) {
  if (!files.length) {
    notify('No files selected.', 'error');
    return;
  }
  if (state.isIngesting) {
    notify('Ingestion already in progress — please wait.', 'warn');
    return;
  }

  state.isIngesting = true;
  clearLog();
  ingestLogDetails.open = true;
  ingestProgress.style.display = 'block';
  progressFill.style.width     = '5%';
  appendLog(`Starting upload of ${files.length} file(s)…`);
  files.forEach(f => appendLog(`  → ${f.name}  (${(f.size/1024).toFixed(1)} KB)`));

  const fd = new FormData();
  files.forEach(f => fd.append('files', f));
  fd.append('tier', tier);

  diag.group('Ingestion');
  diag.info('POST /api/ingest with', files.length, 'file(s) for tier:', tier);

  try {
    const r    = await fetch('/api/ingest', { method: 'POST', body: fd });
    const data = await r.json();
    diag.info('Ingest response:', data);

    if (!r.ok || data.error) {
      const msg = data.error || `HTTP ${r.status}`;
      appendLog(`❌ Upload rejected: ${msg}`);
      notify(`Upload failed: ${msg}`, 'error', 8000);
      diag.error('Ingest API error:', msg);
      return;
    }

    if (data.rejected?.length) {
      data.rejected.forEach(rej => {
        appendLog(`⚠️ Rejected: ${rej}`);
        notify(`Rejected: ${rej}`, 'warn', 7000);
      });
    }

    appendLog(`✅ Uploaded ${data.files.length} file(s). Job ID: ${data.job_id}`);
    ingestStatusTxt.textContent = `Processing ${data.files.join(', ')}…`;
    progressFill.style.width    = '20%';
    state.currentJobId = data.job_id;
    pollJobStatus(data.job_id);

  } catch(err) {
    appendLog(`❌ Network error: ${err.message}`);
    diag.error('Ingest fetch error:', err);
    notify(`Upload failed: ${err.message}`, 'error', 8000);
    state.isIngesting = false;
    ingestProgress.style.display = 'none';
  } finally {
    diag.groupEnd();
  }
}

async function pollJobStatus(jobId) {
  const poll = async () => {
    try {
      const r    = await fetch(`/api/ingest/status/${jobId}`);
      if (!r.ok) throw new Error(`Status HTTP ${r.status}`);
      const data = await r.json();

      if (data.error) {
        appendLog(`❌ Job error: ${data.error}`);
        notify(data.error, 'error', 8000);
        state.isIngesting = false;
        ingestProgress.style.display = 'none';
        return;
      }

      // Sync new log lines
      const allLog = data.log || [];
      const shown  = ingestLog.childElementCount;
      allLog.slice(shown).forEach(line => appendLog(line));

      const pct = Math.min(20 + (data.results.length / Math.max(data.total, 1)) * 70, 90);
      progressFill.style.width    = `${pct}%`;
      ingestStatusTxt.textContent = `Processing… ${data.results.length}/${data.total} file(s) done`;

      if (data.status === 'done') {
        progressFill.style.width = '100%';
        const ok  = data.results.filter(r => r.ok).length;
        const bad = data.results.filter(r => !r.ok).length;

        // Show per-file results
        data.results.forEach(res => {
          if (res.ok) {
            appendLog(`✅ ${res.file}: ${res.result}`);
          } else {
            appendLog(`❌ ${res.file}: FAILED — ${res.result}`);
            diag.error('File failed:', res.file, res.result);
          }
        });

        ingestStatusTxt.textContent = `Done — ${ok} succeeded${bad ? `, ${bad} failed` : ''}`;
        if (ok)  notify(`Ingested ${ok} file(s) successfully`, 'success', 5000);
        if (bad) notify(`${bad} file(s) failed — see diagnostic log`, 'error', 8000);
        if (data.rejected?.length)
          notify(`${data.rejected.length} file(s) rejected (unsupported type)`, 'warn', 7000);

        setTimeout(() => {
          ingestProgress.style.display = 'none';
          progressFill.style.width     = '0%';
        }, 3000);

        state.isIngesting = false;
        fileInput.value   = '';
        await loadDocuments();
        await refreshStatus();
        diag.info('Ingestion complete — ok:', ok, 'failed:', bad);
      } else {
        setTimeout(poll, 1200);
      }
    } catch(err) {
      diag.error('pollJobStatus error:', err);
      appendLog(`⚠️ Poll error: ${err.message} — retrying…`);
      setTimeout(poll, 3000);
    }
  };
  poll();
}

// ── Default prompt buttons ─────────────────────────────────────────────────────
$('preset-gen').addEventListener('click', async () => {
  diag.info('Probing gen_llm server…');
  notify('Testing Gen LLM server (port 8002)…', 'info', 3000);
  const btn = $('preset-gen');
  btn.disabled = true;
  try {
    const r    = await fetch('/api/probe/gen', { method: 'POST' });
    const data = await r.json();
    diag.info('Gen LLM probe result:', data);
    if (data.ok) {
      addChatMsg(
        `🧠 <strong>Gen LLM probe succeeded</strong><br>` +
        `Model: <code>${escHtml(data.model)}</code><br>` +
        `Response: <em>${escHtml(data.response)}</em>`,
        'assistant'
      );
      renderOutput(`## Gen LLM Test ✅\n**Model:** \`${data.model}\`\n\n**Response:** ${data.response}`);
      notify('Gen LLM is online and responding!', 'success');
    } else {
      addChatMsg(`❌ Gen LLM offline: ${escHtml(data.error)}`, 'assistant');
      notify(`Gen LLM probe failed: ${data.error}`, 'error', 8000);
    }
  } catch(err) {
    diag.error('Gen probe fetch error:', err);
    notify(`Gen LLM probe error: ${err.message}`, 'error', 8000);
  }
  btn.disabled = false;
});

$('preset-embed').addEventListener('click', async () => {
  diag.info('Probing embed_llm server…');
  notify('Testing Embed LLM server (port 8003)…', 'info', 3000);
  const btn = $('preset-embed');
  btn.disabled = true;
  try {
    const r    = await fetch('/api/probe/embed', { method: 'POST' });
    const data = await r.json();
    diag.info('Embed LLM probe result:', data);
    if (data.ok) {
      addChatMsg(
        `🔢 <strong>Embed LLM probe succeeded</strong><br>` +
        `Model: <code>${escHtml(data.model)}</code><br>` +
        `Dimension: <strong>${data.dim}</strong> · ` +
        `Sample: <code>[${data.sample.map(v => v.toFixed(4)).join(', ')}…]</code>`,
        'assistant'
      );
      renderOutput(
        `## Embed LLM Test ✅\n**Model:** \`${data.model}\`\n\n` +
        `**Embedding dimension:** ${data.dim}\n\n` +
        `**First 5 values:** \`[${data.sample.map(v => v.toFixed(6)).join(', ')}]\``
      );
      notify(`Embed LLM is online! Dim=${data.dim}`, 'success');
    } else {
      addChatMsg(`❌ Embed LLM offline: ${escHtml(data.error)}`, 'assistant');
      notify(`Embed LLM probe failed: ${data.error}`, 'error', 8000);
    }
  } catch(err) {
    diag.error('Embed probe fetch error:', err);
    notify(`Embed LLM probe error: ${err.message}`, 'error', 8000);
  }
  btn.disabled = false;
});

// ── Query / Chat ───────────────────────────────────────────────────────────────
document.querySelectorAll('.prompt-fill-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    queryInput.value = btn.innerText;
    queryInput.style.height = 'auto';
    queryInput.style.height = Math.min(queryInput.scrollHeight, 160) + 'px';
    submitQuery();
  });
});

queryInput.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    submitQuery();
  }
});
queryInput.addEventListener('input', () => {
  queryInput.style.height = 'auto';
  queryInput.style.height = Math.min(queryInput.scrollHeight, 160) + 'px';
});
sendBtn.addEventListener('click', submitQuery);

function addChatMsg(html, role) {
  const wrap   = document.createElement('div');
  wrap.className = `chat-msg ${role}`;
  const avatar = role === 'user' ? '👤' : '🏥';
  const time   = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  wrap.innerHTML = `
    <div class="chat-avatar">${avatar}</div>
    <div>
      <div class="chat-bubble">${role === 'user' ? escHtml(html) : html}</div>
      <div class="chat-time">${time}</div>
    </div>`;
  chatHistory.appendChild(wrap);
  wrap.scrollIntoView({ behavior: 'smooth', block: 'end' });
  return wrap;
}

function addThinkingMsg(qId) {
  const wrap = document.createElement('div');
  wrap.className = 'chat-msg assistant';
  wrap.innerHTML = `
    <div class="chat-avatar">🏥</div>
    <div>
      <div class="chat-bubble">
        <div class="milestone-graph" id="milestone-graph-${qId}">
          <div class="milestone" id="ms-inference-${qId}">
            <div class="milestone-dot"></div>
            <span>Inference <span class="milestone-timer" data-time="0">(0.0s)</span></span>
          </div>
          <div class="milestone-line"></div>
          <div class="milestone" id="ms-gatekeeping-${qId}">
            <div class="milestone-dot"></div>
            <span>Gatekeeping <span class="milestone-timer" data-time="0">(0.0s)</span></span>
          </div>
          <div class="milestone-line"></div>
          <div class="milestone" id="ms-analysis-${qId}">
            <div class="milestone-dot"></div>
            <span>Analysis <span class="milestone-timer" data-time="0">(0.0s)</span></span>
          </div>
        </div>
      </div>
    </div>`;
  chatHistory.appendChild(wrap);
  wrap.scrollIntoView({ behavior: 'smooth', block: 'end' });
  return wrap;
}

async function submitQuery() {
  const q = queryInput.value.trim();
  if (!q || state.isQuerying) return;

  state.isQuerying  = true;
  sendBtn.disabled  = true;
  queryInput.value  = '';
  queryInput.style.height = 'auto';

  diag.group('Query');
  diag.info('Query:', q);
  addChatMsg(q, 'user');
  const qId = Date.now();
  const thinkingEl = addThinkingMsg(qId);

  outputContainer.innerHTML = `<div class="output-placeholder">
    <div class="ph-icon">⚙️</div>
    <div>Analyzing healthcare policy documents…</div>
  </div>`;
  citationsBlock.innerHTML = '';
  copyBtn.style.display    = 'none';

  let fullText = '';
  let chunkCount = 0;

  try {
    diag.info('POST /api/query');
    const resp = await fetch('/api/query', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ query: q }),
    });

    if (!resp.ok) {
      const err = await resp.json();
      throw new Error(err.error || `HTTP ${resp.status}`);
    }

    const reader  = resp.body.getReader();
    const decoder = new TextDecoder();
    let   buffer  = '';
    
    // Timer logic
    let currentTimerInterval = null;
    let currentMsId = null;
    let currentStartTime = 0;
    
    function startTimer(msId) {
      if (currentTimerInterval) clearInterval(currentTimerInterval);
      currentMsId = msId;
      currentStartTime = Date.now();
      currentTimerInterval = setInterval(() => {
        const el = $(currentMsId);
        if (!el) return;
        const timerSpan = el.querySelector('.milestone-timer');
        if (timerSpan) {
          const s = (Date.now() - currentStartTime) / 1000;
          timerSpan.innerText = `(${s.toFixed(1)}s)`;
          timerSpan.dataset.time = s.toFixed(1);
        }
      }, 100);
    }
    
    function stopTimer() {
      if (currentTimerInterval) clearInterval(currentTimerInterval);
      currentTimerInterval = null;
    }

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let payload;
        try { payload = JSON.parse(line.slice(6)); }
        catch(pe) { diag.warn('SSE parse error:', pe, line); continue; }

        if (payload.error)              throw new Error(payload.error);
        if (payload.status) {
          diag.info('SSE status:', payload.status);
          const msInference = $(`ms-inference-${qId}`);
          const msGatekeeping = $(`ms-gatekeeping-${qId}`);
          const msAnalysis = $(`ms-analysis-${qId}`);
          
          if (!msInference || !msGatekeeping || !msAnalysis) continue;

          // Clear current executing dots
          [`ms-inference-${qId}`, `ms-gatekeeping-${qId}`, `ms-analysis-${qId}`].forEach(id => {
             const el = $(id);
             if (el) {
                 const dot = el.querySelector('.milestone-dot');
                 if (dot.className.includes('executing')) {
                     dot.className = 'milestone-dot complete';
                 }
             }
          });

          if (payload.status === 'inference') {
            msInference.querySelector('.milestone-dot').className = 'milestone-dot executing';
            startTimer(`ms-inference-${qId}`);
          } else if (payload.status === 'gatekeeping') {
            msGatekeeping.querySelector('.milestone-dot').className = 'milestone-dot executing';
            startTimer(`ms-gatekeeping-${qId}`);
          } else if (payload.status === 'analysis') {
            msAnalysis.querySelector('.milestone-dot').className = 'milestone-dot executing';
            startTimer(`ms-analysis-${qId}`);
          }
        }
        if (payload.chunk) {
          if (currentMsId) {
            const el = $(currentMsId);
            if (el) {
              const dot = el.querySelector('.milestone-dot');
              if (dot && dot.className.includes('executing')) {
                if (currentMsId.includes('gatekeeping')) {
                  dot.className = 'milestone-dot failed';
                } else {
                  dot.className = 'milestone-dot complete';
                }
                stopTimer();
              }
            }
          }
          fullText += payload.chunk;
          chunkCount++;
          renderOutput(fullText);
        }
        if (payload.metrics) {
          const m = payload.metrics;
          const min = Math.floor(m.time_seconds / 60);
          const sec = Math.round(m.time_seconds % 60);
          const timeStr = min > 0 ? `${min}m ${sec}s` : `${sec}s`;
          const carbonStr = m.carbon_kg < 0.001 ? "< 1g" : (m.carbon_kg * 1000).toFixed(2) + "g";
          
          fullText += `\n\n---\n**📊 Inference Metrics**\n- **Tokens**: ${m.tokens_in} in / ${m.tokens_out} out\n- **Time**: ${timeStr}\n- **Carbon Footprint**: ${carbonStr} CO₂`;
          renderOutput(fullText);
        }
        if (payload.done) { diag.info('SSE: done'); break; }
      }
    }

    diag.info(`Query complete — ${chunkCount} SSE chunks, ${fullText.length} chars`);
    stopTimer();
    // Do not remove thinkingEl to preserve the graph
    // thinkingEl.remove();
    addChatMsg('Answer generated — see the Output panel →', 'assistant');
    extractAndShowCitations(fullText);
    copyBtn.style.display = 'block';
    state.lastAnswer = fullText;
    notify('Answer ready!', 'success', 2500);

  } catch(err) {
    diag.error('Query error:', err);
    stopTimer();
    // Mark active dot as failed
    if (currentMsId) {
      const el = $(currentMsId);
      if (el) el.querySelector('.milestone-dot').className = 'milestone-dot failed';
    }
    // thinkingEl.remove();
    addChatMsg(`❌ ${escHtml(err.message)}`, 'assistant');
    outputContainer.innerHTML = `<div class="output-placeholder" style="color:var(--red)">
      <div class="ph-icon">⚠️</div><div>${escHtml(err.message)}</div>
    </div>`;
    notify(err.message, 'error', 8000);
  } finally {
    state.isQuerying = false;
    sendBtn.disabled = false;
    diag.groupEnd();
  }
}

// ── Output rendering ───────────────────────────────────────────────────────────
function renderOutput(mdText) {
  outputContainer.innerHTML = marked.parse(mdText);
  outputContainer.scrollTop = outputContainer.scrollHeight;
}

function extractAndShowCitations(mdText) {
  const matches = [...new Set(mdText.match(/\[Source:\s*([^\]]+)\]/g) || [])];
  if (!matches.length) return;
  citationsBlock.innerHTML = '<div style="font-size:11px;color:var(--text-muted);margin-bottom:6px;">📌 Sources</div>'
    + matches.map(m => `<span class="citation-tag">📎 ${escHtml(m)}</span>`).join('');
}

// ── Copy button ────────────────────────────────────────────────────────────────
copyBtn.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(outputContainer.innerText);
    copyBtn.textContent = '✓ Copied!';
    notify('Response copied to clipboard', 'info', 2000);
  } catch(err) {
    diag.error('Clipboard write failed:', err);
    notify('Could not copy — try selecting text manually', 'warn');
  }
  setTimeout(() => { copyBtn.textContent = '⎘ Copy Response'; }, 2000);
});

// ── Utils ──────────────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Init ───────────────────────────────────────────────────────────────────────
(async () => {
  diag.info('App initialising…');
  await refreshStatus();
  await loadDocuments();
  setInterval(refreshStatus, 30_000);
  diag.info('App ready.');
})();
