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

// ── Device detection ──────────────────────────────────────────────────────────
// Applies `body.is-mobile` when the device is a phone/touch device OR the
// viewport is narrower than 768px.  Re-evaluated on every resize so the layout
// adapts when the window is resized (e.g. DevTools responsive mode).
const MOBILE_BREAKPOINT = 768;

function detectDevice() {
  const isTouchDevice  = ('ontouchstart' in window) || (navigator.maxTouchPoints > 0);
  const isNarrow       = window.innerWidth <= MOBILE_BREAKPOINT;
  const isMobileUA     = /Mobi|Android|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
  const isMobile       = isTouchDevice || isNarrow || isMobileUA;

  if (isMobile) {
    document.body.classList.add('is-mobile');
  } else {
    document.body.classList.remove('is-mobile');
  }
  diag.info(`Device detected — mobile: ${isMobile} (touch:${isTouchDevice}, narrow:${isNarrow}, mobileUA:${isMobileUA})`);
  return isMobile;
}

// Run immediately so the correct class is present before first paint
detectDevice();
// Re-run on resize (debounced to avoid excessive calls)
let _resizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(_resizeTimer);
  _resizeTimer = setTimeout(detectDevice, 150);
});

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
const metricsBanner   = $('metrics-banner');
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
    // Admin controls: only show if server reports admin mode enabled AND caller is admin
    const adminMode = (window.APP_CONFIG && window.APP_CONFIG.adminMode !== false) ? true : false;
    const showAdmin = state.isAdmin && adminMode;
    const adminBadge = $('admin-badge');
    if (adminBadge) adminBadge.style.display = showAdmin ? 'inline' : 'none';
    const adminControls = $('admin-controls');
    if (adminControls) adminControls.style.display = showAdmin ? 'flex' : 'none';

    setPill('status-vector', vecOk,   `Vector · ${d.vector_db?.chunks ?? '?'} chunks`);
    setPill('status-graph',  graphOk, graphOk ? `Kuzu DB · ${d.graph_db.nodes} nodes, ${d.graph_db.relationships} edges` : 'Kuzu DB · offline');
    
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
  if (!confirm("Are you sure you want to completely wipe ChromaDB and Kuzu? This cannot be undone.")) return;
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

if ($('db-up-btn'))    $('db-up-btn').addEventListener('click',      () => dockerAction('up'));
if ($('db-down-btn'))  $('db-down-btn').addEventListener('click',    () => dockerAction('down'));
if ($('db-purge-btn')) $('db-purge-btn').addEventListener('click',   adminPurge);
if ($('app-kill-btn')) $('app-kill-btn').addEventListener('click',   adminKill);
if ($('docker-modal-close')) $('docker-modal-close').addEventListener('click', () => { dockerModal.style.display = 'none'; });

// ── Resource Monitor Banner ────────────────────────────────────────────────────
function _resBarClass(pct) {
  if (pct >= 90) return 'crit';
  if (pct >= 70) return 'warn';
  return '';
}

async function pollSysInfo() {
  try {
    const r = await fetch('/api/sysinfo');
    if (!r.ok) return;
    const d = await r.json();
    if (d.error) return;

    // CPU
    const cpuPct   = d.cpu_pct ?? 0;
    const cpuCls   = _resBarClass(cpuPct);
    const cpuBar   = $('res-cpu-bar');
    const cpuPctEl = $('res-cpu-pct');
    const cpuLabel = $('res-cpu-label');
    if (cpuBar)   { cpuBar.style.width = cpuPct + '%'; cpuBar.className = 'res-bar ' + cpuCls; }
    if (cpuPctEl) { cpuPctEl.textContent = cpuPct.toFixed(0) + '%'; cpuPctEl.className = 'res-pct ' + cpuCls; }
    if (cpuLabel) {
      // Shorten: show first word of brand + core count
      const brand = (d.cpu_brand || 'CPU').split(' ').slice(0,2).join(' ');
      const mhz   = d.cpu_mhz ? ` @ ${(d.cpu_mhz/1000).toFixed(1)}GHz` : '';
      cpuLabel.textContent = `${brand} ×${d.cpu_cores}${mhz}`;
    }

    // RAM
    const ramPct   = d.ram_pct ?? 0;
    const ramCls   = _resBarClass(ramPct);
    const ramBar   = $('res-ram-bar');
    const ramPctEl = $('res-ram-pct');
    const ramLabel = $('res-ram-label');
    if (ramBar)   { ramBar.style.width = ramPct + '%'; ramBar.className = 'res-bar ' + ramCls; }
    if (ramPctEl) { ramPctEl.textContent = ramPct.toFixed(0) + '%'; ramPctEl.className = 'res-pct ' + ramCls; }
    if (ramLabel) ramLabel.textContent = `RAM: ${d.ram_used_gb} / ${d.ram_total_gb} GB`;

    // Disk
    const diskPct   = d.disk_pct ?? 0;
    const diskCls   = _resBarClass(diskPct);
    const diskBar   = $('res-disk-bar');
    const diskPctEl = $('res-disk-pct');
    const diskLabel = $('res-disk-label');
    if (diskBar)   { diskBar.style.width = diskPct + '%'; diskBar.className = 'res-bar ' + diskCls; }
    if (diskPctEl) { diskPctEl.textContent = diskPct.toFixed(0) + '%'; diskPctEl.className = 'res-pct ' + diskCls; }
    if (diskLabel) diskLabel.textContent = `Disk: ${d.disk_free_gb} / ${d.disk_total_gb} GB free`;

    // Mode badge
    const badge = $('res-mode-badge');
    if (badge) {
      const isHf = d.hf_mode || (window.APP_CONFIG && window.APP_CONFIG.hfMode);
      badge.textContent  = isHf ? '⚡ HF / CPU Mode' : '🖥 GPU Mode';
      badge.className    = 'res-mode-badge ' + (isHf ? 'hf-mode' : 'gpu-mode');
    }

    // Graph Extraction Active
    const activeGraph = $('status-graph-active');
    const dbGraph = $('status-graph');
    if (d.active_graph_tasks > 0) {
      if (activeGraph) activeGraph.style.display = 'flex';
      if (dbGraph) dbGraph.style.display = 'none';
    } else {
      if (activeGraph) activeGraph.style.display = 'none';
      if (dbGraph) dbGraph.style.display = 'flex';
    }
  } catch(err) {
    diag.warn('pollSysInfo failed:', err);
  }
}

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
let expectedCaptchaAnswer = 0;
const captchaModal = $('captcha-modal');
const captchaQuestion = $('captcha-question');
const captchaInput = $('captcha-input');
const captchaError = $('captcha-error');
const captchaCancel = $('captcha-modal-cancel');
const captchaVerify = $('captcha-modal-verify');

function generateCaptcha() {
    const a = Math.floor(Math.random() * 10) + 1;
    const b = Math.floor(Math.random() * 10) + 1;
    expectedCaptchaAnswer = a + b;
    if (captchaQuestion) captchaQuestion.textContent = `${a} + ${b} =`;
    if (captchaInput) captchaInput.value = '';
    if (captchaError) captchaError.style.display = 'none';
}

if (tierConfirm) {
    tierConfirm.addEventListener('click', () => {
        tierModal.style.display = 'none';
        generateCaptcha();
        if (captchaModal) captchaModal.style.display = 'flex';
        if (captchaInput) captchaInput.focus();
    });
}

if (captchaCancel) {
    captchaCancel.addEventListener('click', () => {
        captchaModal.style.display = 'none';
        pendingFiles = [];
    });
}

if (captchaVerify) {
    const verifyAndProceed = () => {
        const userAns = parseInt(captchaInput.value, 10);
        if (userAns === expectedCaptchaAnswer) {
            captchaModal.style.display = 'none';
            const selectedTier = document.querySelector('input[name="kb_tier"]:checked').value;
            ingestFiles(pendingFiles, selectedTier);
        } else {
            captchaError.style.display = 'block';
            captchaInput.value = '';
            captchaInput.focus();
        }
    };
    captchaVerify.addEventListener('click', verifyAndProceed);
    if (captchaInput) {
        captchaInput.addEventListener('keydown', e => {
            if (e.key === 'Enter') verifyAndProceed();
        });
    }
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
          <div class="milestone" id="ms-graph-${qId}">
            <div class="milestone-dot"></div>
            <div>
              <span>GraphDB <span class="milestone-timer" data-time="0">(0.0s)</span></span>
              <div class="milestone-chunks"></div>
            </div>
          </div>
          <div class="milestone-line"></div>
          <div class="milestone" id="ms-vector-${qId}">
            <div class="milestone-dot"></div>
            <div>
              <span>Vector DB <span class="milestone-timer" data-time="0">(0.0s)</span></span>
              <div class="milestone-chunks"></div>
            </div>
          </div>
          <div class="milestone-line"></div>
          <div class="milestone" id="ms-bm25-${qId}">
            <div class="milestone-dot"></div>
            <div>
              <span>BM25 Search <span class="milestone-timer" data-time="0">(0.0s)</span></span>
              <div class="milestone-chunks"></div>
            </div>
          </div>
          <div class="milestone-line"></div>
          <div class="milestone" id="ms-ranking-${qId}">
            <div class="milestone-dot"></div>
            <span>Cross-Encoder Reranking <span class="milestone-timer" data-time="0">(0.0s)</span></span>
          </div>
          <div class="milestone-line"></div>
          <div class="milestone" id="ms-analysis-${qId}">
            <div class="milestone-dot"></div>
            <div>
              <span>LLM Analysis <span class="milestone-timer" data-time="0">(0.0s)</span></span>
              <div class="milestone-tokens"></div>
            </div>
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

  // On mobile, scroll the chat panel into view so the user sees the response
  if (document.body.classList.contains('is-mobile')) {
    const chatPanel = document.getElementById('chat-panel');
    if (chatPanel) chatPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  const qId = Date.now();
  const thinkingEl = addThinkingMsg(qId);

  outputContainer.innerHTML = `<div class="output-placeholder">
    <div class="ph-icon">⚙️</div>
    <div>Analyzing healthcare policy documents…</div>
  </div>`;
  metricsBanner.style.display = 'none';
  metricsBanner.innerHTML     = '';
  citationsBlock.innerHTML    = '';
  copyBtn.style.display       = 'none';

  let fullText  = '';
  let chunkCount = 0;

  // Hoisted outside try{} so catch{} and finally{} can access them
  // (let/function inside try{} are block-scoped and invisible to catch{})
  let currentTimerInterval = null;
  let currentMsId          = null;
  let currentStartTime     = 0;

  function startTimer(msId) {
    if (currentTimerInterval) clearInterval(currentTimerInterval);
    currentMsId       = msId;
    currentStartTime  = Date.now();
    currentTimerInterval = setInterval(() => {
      const el = $(currentMsId);
      if (!el) return;
      const timerSpan = el.querySelector('.milestone-timer');
      if (timerSpan) {
        const s = (Date.now() - currentStartTime) / 1000;
        timerSpan.innerText    = `(${s.toFixed(1)}s)`;
        timerSpan.dataset.time = s.toFixed(1);
      }
    }, 100);
  }

  function stopTimer() {
    if (currentTimerInterval) clearInterval(currentTimerInterval);
    currentTimerInterval = null;
  }

  try {
    const topK = parseInt($('top-k-select')?.value || 5, 10);
    const maxTokens = parseInt($('max-tokens-select')?.value || 1024, 10);
    const useVector = $('chk-vector')?.checked ?? true;
    const useGraph = $('chk-graph')?.checked ?? true;
    const useBm25 = $('chk-bm25')?.checked ?? true;
    diag.info('POST /api/query', { query: q, top_k: topK, max_tokens: maxTokens, use_vector: useVector, use_graph: useGraph, use_bm25: useBm25 });
    
    // Grey out disabled milestones
    if (!useGraph) $(`ms-graph-${qId}`)?.classList.add('disabled');
    if (!useVector) $(`ms-vector-${qId}`)?.classList.add('disabled');
    if (!useBm25) $(`ms-bm25-${qId}`)?.classList.add('disabled');

    const resp = await fetch('/api/query', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ query: q, top_k: topK, max_tokens: maxTokens, use_vector: useVector, use_graph: useGraph, use_bm25: useBm25 }),
    });

    if (!resp.ok) {
      const errData = await resp.json();
      throw new Error(errData.error || `HTTP ${resp.status}`);
    }

    const reader  = resp.body.getReader();
    const decoder = new TextDecoder();
    let   buffer  = '';

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

        if (payload.error) throw new Error(payload.error);

        if (payload.status) {
          diag.info('SSE status:', payload.status);
          const msGraph     = $(`ms-graph-${qId}`);
          const msVector    = $(`ms-vector-${qId}`);
          const msBm25      = $(`ms-bm25-${qId}`);
          const msRanking   = $(`ms-ranking-${qId}`);
          const msAnalysis  = $(`ms-analysis-${qId}`);
          if (!msGraph || !msAnalysis) continue;

          // Mark previous executing dot as complete before starting next
          [msGraph, msVector, msBm25, msRanking, msAnalysis].forEach(el => {
            if (!el) return;
            const dot = el.querySelector('.milestone-dot');
            if (dot && dot.className.includes('executing')) {
              dot.className = 'milestone-dot complete';
            }
          });

          if (payload.status === 'graph') {
            msGraph.querySelector('.milestone-dot').className = 'milestone-dot executing';
            startTimer(`ms-graph-${qId}`);
          } else if (payload.status === 'inference' || payload.status === 'vector') {
            stopTimer();
            if(msGraph) msGraph.querySelector('.milestone-dot').className = 'milestone-dot complete';
            if(msVector) msVector.querySelector('.milestone-dot').className = 'milestone-dot executing';
            startTimer(`ms-vector-${qId}`);
          } else if (payload.status === 'bm25') {
            stopTimer();
            if(msVector) msVector.querySelector('.milestone-dot').className = 'milestone-dot complete';
            if(msBm25) msBm25.querySelector('.milestone-dot').className = 'milestone-dot executing';
            startTimer(`ms-bm25-${qId}`);
          } else if (payload.status === 'reranking') {
            stopTimer();
            if(msBm25) msBm25.querySelector('.milestone-dot').className = 'milestone-dot complete';
            if(msRanking) msRanking.querySelector('.milestone-dot').className = 'milestone-dot executing';
            startTimer(`ms-ranking-${qId}`);
          } else if (payload.status === 'gatekeeping') {
            // Gatekeeper is instant
            stopTimer();
            if(msRanking) msRanking.querySelector('.milestone-dot').className = 'milestone-dot complete';
          } else if (payload.status === 'analysis') {
            stopTimer();
            if(msRanking) msRanking.querySelector('.milestone-dot').className = 'milestone-dot complete';
            if(msAnalysis) msAnalysis.querySelector('.milestone-dot').className = 'milestone-dot executing';
            startTimer(`ms-analysis-${qId}`);
          }
          
          if (payload.chunks !== undefined) {
            let targetMs = null;
            if (payload.status === 'graph') targetMs = msGraph;
            if (payload.status === 'vector') targetMs = msVector;
            if (payload.status === 'bm25') targetMs = msBm25;
            if (targetMs) {
              const chunkDiv = targetMs.querySelector('.milestone-chunks');
              if (chunkDiv) {
                chunkDiv.innerText = `${payload.chunks} chunk(s) retrieved`;
                chunkDiv.classList.add('visible');
              }
            }
          }
        }

        // ── Answer chunks → render output ───────────────────────────
        if (payload.chunk) {
          // Mark analysis milestone complete on first chunk arrival
          if (currentMsId) {
            const el  = $(currentMsId);
            const dot = el ? el.querySelector('.milestone-dot') : null;
            if (dot && dot.className.includes('executing')) {
              dot.className = 'milestone-dot complete';
              stopTimer();
            }
          }
          fullText += payload.chunk;
          chunkCount++;
          renderOutput(fullText);
        }

        // ── Metrics → banner at top of output panel ─────────────────
        if (payload.metrics) {
          const m = payload.metrics;
          const min      = Math.floor(m.time_seconds / 60);
          const sec      = Math.round(m.time_seconds % 60);
          const timeStr  = min > 0 ? `${min}m ${sec}s` : `${sec}s`;
          const carbonStr = m.carbon_kg < 0.001 ? '< 1g' : (m.carbon_kg * 1000).toFixed(2) + 'g';
          renderMetricsBanner(m.tokens_in, m.tokens_out, timeStr, carbonStr);
          
          if (msAnalysis) {
            const tokDiv = msAnalysis.querySelector('.milestone-tokens');
            if (tokDiv) {
              tokDiv.innerText = `In: ${m.tokens_in} | Out: ${m.tokens_out}`;
            }
          }
        }

        if (payload.done) { diag.info('SSE: done'); break; }
      }
    }

    diag.info(`Query complete — ${chunkCount} SSE chunks, ${fullText.length} chars`);
    stopTimer();
    // Ensure all milestone dots are marked complete
    [$(`ms-inference-${qId}`), $(`ms-kuzu-${qId}`), $(`ms-analysis-${qId}`)].forEach(el => {
      if (!el) return;
      const dot = el.querySelector('.milestone-dot');
      if (dot && !dot.className.includes('failed')) dot.className = 'milestone-dot complete';
    });
    addChatMsg('Answer generated — see the Output panel ↓', 'assistant');
    copyBtn.style.display = 'block';
    state.lastAnswer = fullText;
    notify('Answer ready!', 'success', 2500);

    // On mobile, scroll to output panel after answer is ready
    if (document.body.classList.contains('is-mobile')) {
      const outputPanel = document.getElementById('output-panel');
      if (outputPanel) setTimeout(() => outputPanel.scrollIntoView({ behavior: 'smooth', block: 'start' }), 300);
    }

  } catch(err) {
    diag.error('Query error:', err);
    stopTimer();
    if (currentMsId) {
      const el = $(currentMsId);
      if (el) el.querySelector('.milestone-dot').className = 'milestone-dot failed';
    }
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

// ── Metrics banner (pinned at top of output panel) ────────────────────────────
function renderMetricsBanner(tokIn, tokOut, timeStr, carbonStr) {
  metricsBanner.innerHTML = `
    <div class="metrics-banner-inner">
      <span class="metrics-title">📊 Inference Metrics</span>
      <span class="metrics-pill">🔢 <strong>${tokIn}</strong> in / <strong>${tokOut}</strong> out tokens</span>
      <span class="metrics-pill">⏱ <strong>${timeStr}</strong></span>
      <span class="metrics-pill">🌱 <strong>${carbonStr}</strong> CO₂</span>
    </div>`;
  metricsBanner.style.display = 'block';
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

// ── Auto-ingest background progress banner ─────────────────────────────────────

const autoIngestBanner = $('auto-ingest-banner');
const autoIngestBar    = $('auto-ingest-bar');
const autoIngestLabel  = $('auto-ingest-label');
const autoIngestSub    = $('auto-ingest-sub');
const autoIngestCount  = $('auto-ingest-count');

let _autoIngestPollTimer    = null;
let _autoIngestDoneNotified = false;

async function pollAutoIngestStatus() {
  try {
    const r = await fetch('/api/auto-ingest/status');
    if (!r.ok) return;
    const d = await r.json();

    const isActive = d.running || (d.total > 0 && !d.done);
    const isDone   = d.done && d.total > 0;

    if (isActive) {
      // Show and update banner
      if (autoIngestBanner) {
        autoIngestBanner.style.display = 'block';
        document.body.classList.add('ingest-banner-visible');
      }
      const pct = d.total > 0 ? Math.round((d.completed / d.total) * 100) : 5;
      if (autoIngestBar)   autoIngestBar.style.width = pct + '%';
      if (autoIngestLabel) autoIngestLabel.textContent = 'Indexing knowledge base…';
      if (autoIngestSub)   autoIngestSub.textContent   = d.current_file ? `Processing: ${d.current_file}` : 'Preparing…';
      if (autoIngestCount) autoIngestCount.textContent = `${d.completed} / ${d.total}`;

    } else if (isDone && !_autoIngestDoneNotified) {
      // Show completion state briefly then hide
      _autoIngestDoneNotified = true;
      if (autoIngestBanner) autoIngestBanner.style.display = 'block';
      if (autoIngestBar)    autoIngestBar.style.width = '100%';

      const ok  = (d.results || []).filter(x => x.ok).length;
      const bad = (d.results || []).filter(x => !x.ok).length;

      if (autoIngestLabel) autoIngestLabel.textContent = `Knowledge base ready — ${ok} file${ok !== 1 ? 's' : ''} indexed`;
      if (autoIngestSub)   autoIngestSub.textContent   = bad > 0 ? `⚠️ ${bad} file(s) failed` : '✓ All files ingested successfully';
      if (autoIngestCount) autoIngestCount.textContent = `${ok} / ${d.total}`;

      if (ok > 0)  notify(`Auto-ingest complete: ${ok} knowledge base file${ok !== 1 ? 's' : ''} indexed`, 'success', 6000);
      if (bad > 0) notify(`Auto-ingest: ${bad} file(s) failed to ingest`, 'warn', 8000);

      // Refresh document list to show newly ingested docs
      await loadDocuments();
      await refreshStatus();

      // Fade out banner after 4 seconds
      setTimeout(() => {
        if (autoIngestBanner) autoIngestBanner.style.display = 'none';
        document.body.classList.remove('ingest-banner-visible');
      }, 4000);

      // Stop polling
      if (_autoIngestPollTimer) clearInterval(_autoIngestPollTimer);
      _autoIngestPollTimer = null;
      return;

    } else if (d.error && !_autoIngestDoneNotified) {
      _autoIngestDoneNotified = true;
      notify(`Auto-ingest error: ${d.error}`, 'error', 8000);
      if (_autoIngestPollTimer) clearInterval(_autoIngestPollTimer);
      _autoIngestPollTimer = null;
      return;

    } else {
      // Not started yet or no files — keep banner hidden
      if (autoIngestBanner) autoIngestBanner.style.display = 'none';
      document.body.classList.remove('ingest-banner-visible');
    }

  } catch (err) {
    diag.warn('pollAutoIngestStatus error:', err);
  }
}

// ── Init ───────────────────────────────────────────────────────────────────────
(async () => {
  diag.info('App initialising…');
  await refreshStatus();
  await loadDocuments();
  await pollSysInfo();           // Initial resource banner population
  await pollAutoIngestStatus();  // Check if auto-ingest is already running

  setInterval(refreshStatus, 30_000);
  setInterval(pollSysInfo,   10_000);  // Update resource banner every 10 s

  // Poll auto-ingest every 2 s (self-cancels when done)
  _autoIngestPollTimer = setInterval(pollAutoIngestStatus, 2000);

  diag.info('App ready.');
})();
