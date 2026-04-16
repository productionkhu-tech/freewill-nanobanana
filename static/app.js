// NanoBanana Web — Frontend JavaScript
"use strict";

let galleryColumns = 2;
let favoritesOnly = false;
let selectedPaths = [];
let viewerPath = null;
let viewerState = null;
let pollTimer = null;
let logPollTimer = null;
let promptSectionCount = 0;

// ==========================================
// Init
// ==========================================
document.addEventListener("DOMContentLoaded", () => {
  loadSettings();
  loadVersion();
  refreshRefs();
  refreshGallery();
  startPolling();
  addPromptSection();
  setupKeyboardShortcuts();
});

function setupKeyboardShortcuts() {
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !document.getElementById("viewerModal").classList.contains("hidden")) {
      closeViewer();
      return;
    }
    const tag = document.activeElement?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

    if (e.key === "Delete") { deleteSelected(); e.preventDefault(); }
    if (e.key === "f" || e.key === "F") { favSelected(); e.preventDefault(); }
    if (e.ctrlKey && (e.key === "a" || e.key === "A")) { selectAll(); e.preventDefault(); }
    if (e.key === "ArrowLeft") { navigateViewer(-1); e.preventDefault(); }
    if (e.key === "ArrowRight") { navigateViewer(1); e.preventDefault(); }
  });
}

// ==========================================
// API Helpers
// ==========================================
async function api(url, opts = {}) {
  if (opts.body && typeof opts.body === "object" && !(opts.body instanceof FormData)) {
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  const r = await fetch(url, opts);
  return r.json();
}

// ==========================================
// Settings
// ==========================================
async function loadVersion() {
  try {
    const d = await api("/api/version");
    document.getElementById("versionLabel").textContent = d.version || "unknown";
  } catch (e) {
    document.getElementById("versionLabel").textContent = "offline";
  }
}

async function loadSettings() {
  const d = await api("/api/settings");
  document.getElementById("modelSelect").value = d.model;
  document.getElementById("aspectSelect").value = d.aspect;
  document.getElementById("resolutionSelect").value = d.resolution;
  document.getElementById("countSelect").value = String(d.count);
  document.getElementById("folderInput").value = d.output_dir;
  document.getElementById("fixedPrompt").value = d.fixed_prompt || "";
  document.getElementById("namingSwitch").checked = d.naming_enabled;
  document.getElementById("namingPrefix").value = d.naming_prefix || "S010";
  document.getElementById("namingDelimiter").value = d.naming_delimiter || "C010";
  document.getElementById("namingIndexPrefix").value = d.naming_index_prefix || "I";
  document.getElementById("namingPadding").value = String(d.naming_padding || 3);
  galleryColumns = d.gallery_columns || 2;
  updateColumnsUI();
  updateNamingControls();
  updateRefLimitHint(d.model);

  // Restore prompt sections
  const container = document.getElementById("promptSections");
  container.innerHTML = "";
  promptSectionCount = 0;
  if (d.prompt_sections && d.prompt_sections.length > 0) {
    d.prompt_sections.forEach(text => addPromptSection(text));
  } else {
    addPromptSection("");
  }

  // Load API status
  refreshApiStatus();
}

async function saveSettings() {
  const sections = [];
  document.querySelectorAll(".prompt-section-box").forEach(el => sections.push(el.value));
  await api("/api/settings", {
    method: "POST",
    body: {
      model: document.getElementById("modelSelect").value,
      aspect: document.getElementById("aspectSelect").value,
      resolution: document.getElementById("resolutionSelect").value,
      count: parseInt(document.getElementById("countSelect").value),
      output_dir: document.getElementById("folderInput").value,
      fixed_prompt: document.getElementById("fixedPrompt").value,
      prompt_sections: sections,
      naming_enabled: document.getElementById("namingSwitch").checked,
      naming_prefix: document.getElementById("namingPrefix").value,
      naming_delimiter: document.getElementById("namingDelimiter").value,
      naming_index_prefix: document.getElementById("namingIndexPrefix").value,
      naming_padding: parseInt(document.getElementById("namingPadding").value),
      gallery_columns: galleryColumns,
    }
  });
}

function onModelChange() {
  const model = document.getElementById("modelSelect").value;
  updateRefLimitHint(model);
  saveSettings();
}

function updateRefLimitHint(model) {
  const hint = document.getElementById("refLimitHint");
  if (model === "gemini-2.5-flash-image") {
    hint.textContent = "Flash model supports up to 3 reference images.";
  } else {
    hint.textContent = "3rd-gen models support up to 14 reference images.";
  }
}

// ==========================================
// Prompt Sections
// ==========================================
function addPromptSection(initialText = "") {
  promptSectionCount++;
  const container = document.getElementById("promptSections");
  const div = document.createElement("div");
  div.className = "prompt-section";
  div.innerHTML = `<label class="field-label">Prompt ${promptSectionCount}</label>
    <textarea class="prompt-box prompt-section-box" rows="4" placeholder="Describe the image..."></textarea>`;
  div.querySelector("textarea").value = initialText;
  container.appendChild(div);
  updateRemovePromptBtn();
}

function removePromptSection() {
  const container = document.getElementById("promptSections");
  if (container.children.length <= 1) return;
  container.removeChild(container.lastElementChild);
  promptSectionCount = container.children.length;
  // Relabel
  container.querySelectorAll(".field-label").forEach((lbl, i) => lbl.textContent = `Prompt ${i + 1}`);
  updateRemovePromptBtn();
  saveSettings();
}

function updateRemovePromptBtn() {
  const btn = document.getElementById("removePromptBtn");
  const count = document.getElementById("promptSections").children.length;
  btn.disabled = count < 2;
  btn.style.opacity = count < 2 ? "0.5" : "1";
}

function resetSetup() {
  document.getElementById("modelSelect").value = "gemini-3-pro-image-preview";
  document.getElementById("fixedPrompt").value = "";
  const container = document.getElementById("promptSections");
  container.innerHTML = "";
  promptSectionCount = 0;
  addPromptSection("");
  clearRefs(false);
  saveSettings();
  showToast("Reset complete", "success");
}

// ==========================================
// Naming Controls
// ==========================================
function updateNamingControls() {
  const enabled = document.getElementById("namingSwitch").checked;
  const grid = document.getElementById("namingGrid");
  if (enabled) grid.classList.remove("disabled");
  else grid.classList.add("disabled");
}

// ==========================================
// Reference Images
// ==========================================
async function refreshRefs() {
  const d = await api("/api/refs");
  const grid = document.getElementById("refGrid");
  const empty = document.getElementById("refEmpty");
  grid.innerHTML = "";

  if (d.refs.length === 0) {
    empty.style.display = "block";
    return;
  }
  empty.style.display = "none";

  d.refs.forEach((ref, i) => {
    const cell = document.createElement("div");
    cell.className = "ref-cell";
    cell.innerHTML = `
      <img src="/api/refs/thumb/${i}?t=${Date.now()}" alt="ref ${i+1}">
      <div class="ref-actions">
        <button class="ref-btn pin ${ref.pinned ? 'pinned' : ''}" onclick="togglePin(${i})" title="Pin">${ref.pinned ? '📌' : '📎'}</button>
        <button class="ref-btn" onclick="removeRef(${i})" title="Remove">✕</button>
      </div>`;
    grid.appendChild(cell);
  });
}

async function browseRefImages() {
  const d = await api("/api/browse-files", { method: "POST" });
  if (d.ok && d.added > 0) {
    refreshRefs();
    showToast(`Added ${d.added} image(s)`, "success");
  }
}

async function removeRef(idx) {
  await api(`/api/refs/${idx}`, { method: "DELETE" });
  refreshRefs();
}

async function togglePin(idx) {
  await api(`/api/refs/pin/${idx}`, { method: "POST" });
  refreshRefs();
}

async function clearRefs(preservePinned = true) {
  await api("/api/refs/clear", { method: "POST", body: { preserve_pinned: preservePinned } });
  refreshRefs();
}

async function pasteClipboardRef() {
  const d = await api("/api/refs/paste", { method: "POST" });
  if (d.ok) {
    refreshRefs();
    showToast(d.message, "success");
  } else {
    showToast(d.message || "No image in clipboard", "warn");
  }
}

function onRefDragEnter(e) {
  e.preventDefault();
  document.getElementById("refArea").classList.add("dragover");
}
function onRefDragLeave(e) {
  document.getElementById("refArea").classList.remove("dragover");
}
async function onRefDrop(e) {
  e.preventDefault();
  document.getElementById("refArea").classList.remove("dragover");
  const files = e.dataTransfer?.files;
  if (!files || files.length === 0) return;

  const form = new FormData();
  for (const f of files) {
    const ext = f.name.split(".").pop().toLowerCase();
    if (["png","jpg","jpeg","webp","bmp"].includes(ext)) form.append("files", f);
  }
  if ([...form.entries()].length === 0) { showToast("No supported images", "warn"); return; }
  const d = await api("/api/refs/upload", { method: "POST", body: form });
  if (d.added > 0) {
    refreshRefs();
    showToast(`Added ${d.added} image(s)`, "success");
  }
}

// ==========================================
// Gallery
// ==========================================
async function refreshGallery() {
  const d = await api("/api/gallery");
  const grid = document.getElementById("galleryGrid");
  const empty = document.getElementById("emptyState");
  const search = document.getElementById("gallerySearch").value.toLowerCase();

  // Remove non-skeleton children
  grid.querySelectorAll(".card").forEach(c => c.remove());

  let items = d.items;
  if (favoritesOnly) items = items.filter(it => it.favorite);
  if (search) items = items.filter(it =>
    (it.prompt || "").toLowerCase().includes(search) || (it.filename || "").toLowerCase().includes(search)
  );

  if (items.length === 0 && !grid.querySelector(".skeleton")) {
    if (!empty) {
      const e = document.createElement("div");
      e.id = "emptyState";
      e.className = "empty-state";
      e.innerHTML = "No images yet<br>Configure prompt and click Generate";
      grid.appendChild(e);
    } else {
      empty.style.display = "block";
    }
  } else {
    if (empty) empty.style.display = "none";
  }

  document.getElementById("countBadge").textContent = `${d.count} images`;

  items.forEach(item => {
    const card = document.createElement("div");
    card.className = "card" + (selectedPaths.includes(item.filepath) ? " selected" : "");
    card.dataset.path = item.filepath;
    const apiLabel = item.api_used === "vertex" ? "V" : "S";
    card.innerHTML = `
      <img class="card-img" src="/api/gallery/thumb?path=${encodeURIComponent(item.filepath)}&size=${getThumbSize()}"
           loading="lazy" onclick="openViewer('${escHtml(item.filepath)}')" alt="">
      <div class="card-body">
        <div class="card-filename" title="${escHtml(item.filename)}">${escHtml(item.filename)}</div>
        <div class="card-meta">
          ${item.elapsed_sec}s <span class="api-badge">${apiLabel}</span>
          ${item.resolution ? ' &bull; ' + item.resolution : ''} ${item.aspect ? ' &bull; ' + item.aspect : ''}
        </div>
        <div class="card-actions">
          <button class="card-btn fav ${item.favorite ? 'active' : ''}" onclick="event.stopPropagation();toggleFav('${escHtml(item.filepath)}',this)">&#9733;</button>
          <button class="card-btn" onclick="event.stopPropagation();useAsRef('${escHtml(item.filepath)}')">Use as Ref</button>
          <button class="card-btn" onclick="event.stopPropagation();openInExplorer('${escHtml(item.filepath)}')">Explorer</button>
          <button class="card-btn" onclick="event.stopPropagation();showPromptPopup('${escHtml(item.filepath)}','${escHtml(item.prompt)}')">Prompt</button>
          <button class="card-btn" onclick="event.stopPropagation();loadSetup('${escHtml(item.filepath)}')">Load Setup</button>
          <button class="card-btn" onclick="event.stopPropagation();copyToClipboard('${escHtml(item.filepath)}')">Copy</button>
          <button class="card-btn del" onclick="event.stopPropagation();deleteImage('${escHtml(item.filepath)}')">Delete</button>
        </div>
      </div>`;
    card.addEventListener("click", (e) => {
      if (e.target.closest(".card-actions")) return;
      selectCard(item.filepath, e);
    });
    grid.appendChild(card);
  });
}

function getThumbSize() {
  if (galleryColumns <= 2) return 560;
  if (galleryColumns <= 4) return 320;
  return 180;
}

function escHtml(s) {
  return (s || "").replace(/'/g, "\\'").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

function filterGallery() { refreshGallery(); }

function toggleFavFilter() {
  favoritesOnly = !favoritesOnly;
  const btn = document.getElementById("favFilterBtn");
  btn.classList.toggle("active", favoritesOnly);
  refreshGallery();
}

function setColumns(n) {
  galleryColumns = n;
  updateColumnsUI();
  refreshGallery();
  saveSettings();
}

function updateColumnsUI() {
  const grid = document.getElementById("galleryGrid");
  grid.className = `gallery-grid cols-${galleryColumns}`;
  document.querySelectorAll(".layout-btn").forEach(b => {
    b.classList.toggle("active", parseInt(b.dataset.cols) === galleryColumns);
  });
}

function selectCard(filepath, e) {
  if (e && e.ctrlKey) {
    if (selectedPaths.includes(filepath)) {
      selectedPaths = selectedPaths.filter(p => p !== filepath);
    } else {
      selectedPaths.push(filepath);
    }
  } else {
    selectedPaths = [filepath];
  }
  document.querySelectorAll(".card").forEach(c => {
    c.classList.toggle("selected", selectedPaths.includes(c.dataset.path));
  });
}

function selectAll() {
  selectedPaths = [];
  document.querySelectorAll(".card").forEach(c => {
    selectedPaths.push(c.dataset.path);
    c.classList.add("selected");
  });
}

async function deleteImage(filepath) {
  if (!confirm("Delete this image?")) return;
  const d = await api("/api/gallery/delete", { method: "POST", body: { paths: [filepath] } });
  if (d.deleted > 0) {
    selectedPaths = selectedPaths.filter(p => p !== filepath);
    refreshGallery();
    showToast("Deleted", "success");
  } else if (d.errors.length) {
    showToast(d.errors[0], "error");
  }
}

async function deleteSelected() {
  if (selectedPaths.length === 0) return;
  if (!confirm(`Delete ${selectedPaths.length} image(s)?`)) return;
  const d = await api("/api/gallery/delete", { method: "POST", body: { paths: [...selectedPaths] } });
  selectedPaths = [];
  refreshGallery();
  showToast(`Deleted ${d.deleted} image(s)`, "success");
}

async function favSelected() {
  if (selectedPaths.length !== 1) return;
  await toggleFav(selectedPaths[0]);
}

async function toggleFav(filepath, btn) {
  const d = await api("/api/gallery/favorite", { method: "POST", body: { filepath } });
  if (btn) btn.classList.toggle("active", d.favorite);
  else refreshGallery();
}

async function useAsRef(filepath) {
  const d = await api("/api/gallery/use-as-ref", { method: "POST", body: { filepath } });
  if (d.ok) {
    refreshRefs();
    showToast("Added as reference", "success");
  }
}

async function openInExplorer(filepath) {
  await api("/api/gallery/open-explorer", { method: "POST", body: { filepath } });
}

async function loadSetup(filepath) {
  const d = await api("/api/gallery/load-setup", { method: "POST", body: { filepath } });
  if (d.ok) {
    await loadSettings();
    showToast("Loaded saved setup", "success");
  } else {
    showToast(d.error || "Failed", "error");
  }
}

async function copyToClipboard(filepath) {
  const d = await api("/api/copy-to-clipboard", { method: "POST", body: { filepath } });
  showToast(d.ok ? "Copied to clipboard" : (d.error || "Failed"), d.ok ? "success" : "error");
}

function showPromptPopup(filepath, prompt) {
  const decoded = prompt.replace(/\\'/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, '<');
  const text = decoded || "(no prompt)";
  const w = window.open("", "_blank", "width=500,height=300");
  if (w) {
    w.document.write(`<html><head><title>Prompt</title><style>body{background:#2C2C2E;color:#F5F5F7;font-family:sans-serif;padding:20px;white-space:pre-wrap;word-break:break-all;}</style></head><body>${text.replace(/</g,'&lt;')}</body></html>`);
  }
}

// ==========================================
// Generation
// ==========================================
async function generate() {
  await saveSettings();
  const d = await api("/api/generate", { method: "POST" });
  if (!d.ok) {
    showToast(d.error || "Cannot generate", "error");
    return;
  }
  showToast(`Generating ${d.count} image(s)...`, "success");
  updateGenUI(true);

  // Add skeleton cards
  const grid = document.getElementById("galleryGrid");
  const empty = document.getElementById("emptyState");
  if (empty) empty.style.display = "none";
  for (let i = 0; i < d.count; i++) {
    const skel = document.createElement("div");
    skel.className = "skeleton";
    skel.innerHTML = `<div class="skel-img"></div><div class="skel-line"></div><div class="skel-line"></div>
      <div class="skel-chips"><div class="skel-chip" style="width:56px"></div><div class="skel-chip" style="width:88px"></div></div>`;
    grid.insertBefore(skel, grid.firstChild);
  }
}

async function stopGenerate() {
  await api("/api/stop", { method: "POST" });
  showToast("Stopping...", "warn");
}

function updateGenUI(generating) {
  document.getElementById("genBtn").disabled = generating;
  document.getElementById("topGenBtn").disabled = generating;
  document.getElementById("stopBtn").disabled = !generating;
  if (generating) {
    document.getElementById("genBtn").textContent = "Generating...";
  } else {
    document.getElementById("genBtn").textContent = "Generate";
  }
}

// ==========================================
// Polling
// ==========================================
function startPolling() {
  pollTimer = setInterval(pollEvents, 800);
  logPollTimer = setInterval(pollLogs, 2000);
  setInterval(refreshApiStatus, 10000);
}

async function pollEvents() {
  const d = await api("/api/events");
  if (!d.events || d.events.length === 0) return;

  for (const ev of d.events) {
    if (ev.type === "image_done") {
      // Remove one skeleton
      const skel = document.querySelector(".skeleton");
      if (skel) skel.remove();
      refreshGallery();
      updateProgress(ev.done, ev.total);
    } else if (ev.type === "image_failed") {
      const skel = document.querySelector(".skeleton");
      if (skel) skel.remove();
      updateProgress(ev.done + ev.failed, ev.total);
    } else if (ev.type === "done") {
      document.querySelectorAll(".skeleton").forEach(s => s.remove());
      updateGenUI(false);
      refreshGallery();
      const done = ev.done || 0;
      const failed = ev.failed || 0;
      document.getElementById("progressLabel").textContent = `Done  ok ${done}  fail ${failed}`;
      document.getElementById("progressFill").style.width = done > 0 ? "100%" : "0%";
      document.getElementById("statusLabel").textContent = `Completed  ${done} image(s) saved`;
    }
  }
}

function updateProgress(current, total) {
  if (total > 0) {
    document.getElementById("progressFill").style.width = `${(current / total) * 100}%`;
  }
  document.getElementById("progressLabel").textContent = `${current}/${total}`;
  document.getElementById("statusLabel").textContent = `Generating ${current}/${total}...`;
}

async function pollLogs() {
  const d = await api("/api/logs");
  const box = document.getElementById("logBox");
  box.textContent = (d.logs || []).join("\n");
  box.scrollTop = box.scrollHeight;
}

async function refreshApiStatus() {
  const d = await api("/api/status");
  const vDot = document.getElementById("vertexDot");
  const sDot = document.getElementById("studioDot");
  vDot.className = "dot " + d.vertex;
  sDot.className = "dot " + d.studio;
  if (d.is_generating) updateGenUI(true);
}

// ==========================================
// Folder / Project
// ==========================================
async function browseFolder() {
  const d = await api("/api/browse-folder", { method: "POST" });
  if (d.ok) {
    document.getElementById("folderInput").value = d.folder;
    showToast("Folder set", "success");
  }
}

async function saveProject() {
  await saveSettings();
  const d = await api("/api/project/save", { method: "POST", body: {} });
  showToast(d.ok ? "Project saved" : (d.error || "Save failed"), d.ok ? "success" : "error");
}

async function loadProject() {
  const d = await api("/api/browse-project", { method: "POST" });
  if (d.ok) {
    await loadSettings();
    refreshGallery();
    refreshRefs();
    showToast("Project loaded", "success");
  }
}

// ==========================================
// Sidebar Toggle
// ==========================================
document.getElementById("toggleSidebar").addEventListener("click", () => {
  const sb = document.getElementById("sidebar");
  const btn = document.getElementById("toggleSidebar");
  sb.classList.toggle("hidden");
  btn.textContent = sb.classList.contains("hidden") ? "Show Panel" : "Hide Panel";
});

// ==========================================
// Image Viewer
// ==========================================
function openViewer(filepath) {
  const modal = document.getElementById("viewerModal");
  const canvas = document.getElementById("viewerCanvas");
  const ctx = canvas.getContext("2d");

  modal.classList.remove("hidden");
  viewerPath = filepath;

  const img = new window.Image();
  img.onload = () => {
    viewerState = {
      img, scale: 1, offsetX: 0, offsetY: 0, dragging: false, lastX: 0, lastY: 0,
    };
    fitViewer();
  };
  img.src = `/api/gallery/image?path=${encodeURIComponent(filepath)}`;

  document.getElementById("viewerTitle").textContent = filepath.split(/[/\\]/).pop();

  // Load prompt
  fetch(`/api/gallery`).then(r => r.json()).then(d => {
    const item = d.items.find(it => it.filepath === filepath);
    document.getElementById("viewerPrompt").textContent = item ? item.prompt : "";
  });

  canvas.onmousedown = (e) => { if (viewerState) { viewerState.dragging = true; viewerState.lastX = e.clientX; viewerState.lastY = e.clientY; canvas.style.cursor = "grabbing"; } };
  canvas.onmousemove = (e) => {
    if (!viewerState?.dragging) return;
    viewerState.offsetX += e.clientX - viewerState.lastX;
    viewerState.offsetY += e.clientY - viewerState.lastY;
    viewerState.lastX = e.clientX;
    viewerState.lastY = e.clientY;
    renderViewer();
  };
  canvas.onmouseup = () => { if (viewerState) { viewerState.dragging = false; canvas.style.cursor = "grab"; } };
  canvas.onwheel = (e) => {
    if (!viewerState) return;
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    const rect = canvas.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    const imgX = (cx - viewerState.offsetX) / viewerState.scale;
    const imgY = (cy - viewerState.offsetY) / viewerState.scale;
    viewerState.scale = Math.max(0.05, Math.min(10, viewerState.scale * factor));
    viewerState.offsetX = cx - imgX * viewerState.scale;
    viewerState.offsetY = cy - imgY * viewerState.scale;
    renderViewer();
  };
}

function fitViewer() {
  if (!viewerState) return;
  const canvas = document.getElementById("viewerCanvas");
  canvas.width = canvas.clientWidth;
  canvas.height = canvas.clientHeight;
  const s = Math.min(canvas.width / viewerState.img.width, canvas.height / viewerState.img.height);
  viewerState.scale = s;
  viewerState.offsetX = (canvas.width - viewerState.img.width * s) / 2;
  viewerState.offsetY = (canvas.height - viewerState.img.height * s) / 2;
  renderViewer();
}

function renderViewer() {
  if (!viewerState) return;
  const canvas = document.getElementById("viewerCanvas");
  const ctx = canvas.getContext("2d");
  canvas.width = canvas.clientWidth;
  canvas.height = canvas.clientHeight;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  const w = viewerState.img.width * viewerState.scale;
  const h = viewerState.img.height * viewerState.scale;
  ctx.drawImage(viewerState.img, viewerState.offsetX, viewerState.offsetY, w, h);
}

function closeViewer() {
  document.getElementById("viewerModal").classList.add("hidden");
  viewerState = null;
  viewerPath = null;
}

function navigateViewer(step) {
  if (!viewerPath) return;
  const cards = [...document.querySelectorAll(".card")];
  if (cards.length === 0) return;
  const paths = cards.map(c => c.dataset.path);
  let idx = paths.indexOf(viewerPath);
  if (idx === -1) idx = 0;
  else idx = (idx + step + paths.length) % paths.length;
  openViewer(paths[idx]);
}

window.addEventListener("resize", () => { if (viewerState) fitViewer(); });

// ==========================================
// Toast
// ==========================================
let toastTimer = null;
function showToast(msg, kind = "info") {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast " + kind;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.classList.add("hidden"); }, 2200);
}

// ==========================================
// Auto-save settings on change
// ==========================================
["aspectSelect", "resolutionSelect", "countSelect", "namingSwitch",
 "namingPrefix", "namingDelimiter", "namingIndexPrefix", "namingPadding"].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener("change", () => { saveSettings(); });
});
