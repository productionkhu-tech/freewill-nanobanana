// NanoBanana Web — Frontend JavaScript (Full Feature Parity)
"use strict";

let galleryColumns = 2;
let favoritesOnly = false;
let selectedPaths = [];
let selectionAnchor = null;
let viewerPath = null;
let viewerState = null;
let pollTimer = null;
let logPollTimer = null;
let promptSectionCount = 0;
let mentionMenu = null;
let mentionTarget = null;
let refCount = 0;
let allGalleryPaths = []; // ordered paths for shift-select & viewer nav
let isGenerating = false;

// ==========================================
// Init
// ==========================================
document.addEventListener("DOMContentLoaded", async () => {
  await loadSettings();
  loadVersion();
  refreshRefs();
  await refreshGallery();
  startPolling();
  setupKeyboardShortcuts();
  setupClipboardPaste();
  setupFixedPromptMention();
  // Show recent projects if gallery is empty
  checkRecentProjects();
});

// ==========================================
// Keyboard Shortcuts (matching original desktop)
// ==========================================
function setupKeyboardShortcuts() {
  document.addEventListener("keydown", (e) => {
    // Escape: close mention menu → close viewer
    if (e.key === "Escape") {
      if (mentionMenu) { closeMentionMenu(); return; }
      if (!document.getElementById("viewerModal").classList.contains("hidden")) {
        closeViewer(); return;
      }
      return;
    }

    // Ctrl+Enter / Ctrl+NumpadEnter: generate (works even in text fields)
    if (e.ctrlKey && e.key === "Enter") {
      e.preventDefault(); generate(); return;
    }

    const tag = document.activeElement?.tagName;
    const isText = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";

    // Ctrl+C: copy selected image (not in text fields)
    if (e.ctrlKey && (e.key === "c" || e.key === "C") && !isText) {
      if (selectedPaths.length === 1) { copyToClipboard(selectedPaths[0]); e.preventDefault(); }
      return;
    }

    // Ctrl+A: select all gallery
    if (e.ctrlKey && (e.key === "a" || e.key === "A") && !isText) {
      selectAll(); e.preventDefault(); return;
    }

    if (isText) return;

    // Delete: delete selected
    if (e.key === "Delete") { deleteSelected(); e.preventDefault(); }
    // F: toggle favorite
    if (e.key === "f" || e.key === "F") { favSelected(); e.preventDefault(); }
    // Arrow keys: viewer navigation
    if (e.key === "ArrowLeft") { navigateViewer(-1); e.preventDefault(); }
    if (e.key === "ArrowRight") { navigateViewer(1); e.preventDefault(); }
  });
}

// ==========================================
// Clipboard Paste → Reference Image (Ctrl+V)
// ==========================================
function setupClipboardPaste() {
  document.addEventListener("paste", async (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    let hasImage = false;
    for (const item of items) {
      if (item.type.startsWith("image/")) { hasImage = true; break; }
    }
    if (!hasImage) return;
    e.preventDefault();
    for (const item of items) {
      if (item.type.startsWith("image/")) {
        const blob = item.getAsFile();
        if (!blob) continue;
        const form = new FormData();
        form.append("files", blob, `clipboard_${Date.now()}.png`);
        const d = await api("/api/refs/upload", { method: "POST", body: form });
        if (d.added > 0) { refreshRefs(); showToast("Pasted image as reference", "success"); }
        return;
      }
    }
  });
}

// ==========================================
// API Helper
// ==========================================
async function api(url, opts = {}) {
  if (opts.body && typeof opts.body === "object" && !(opts.body instanceof FormData)) {
    opts.headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
    opts.body = JSON.stringify(opts.body);
  }
  try {
    const r = await fetch(url, opts);
    return r.json();
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

// ==========================================
// Version
// ==========================================
async function loadVersion() {
  try {
    const d = await api("/api/version");
    document.getElementById("versionLabel").textContent = d.version || "unknown";
  } catch (e) {
    document.getElementById("versionLabel").textContent = "offline";
  }
}

// ==========================================
// Settings
// ==========================================
async function loadSettings() {
  const d = await api("/api/settings");
  if (!d.model) return;
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
  hint.textContent = model === "gemini-2.5-flash-image"
    ? "Flash model supports up to 3 reference images."
    : "3rd-gen models support up to 14 reference images.";
}

// ==========================================
// Prompt Sections
// ==========================================
function addPromptSection(initialText = "") {
  promptSectionCount++;
  const container = document.getElementById("promptSections");
  const div = document.createElement("div");
  div.className = "prompt-section";

  const label = document.createElement("label");
  label.className = "field-label";
  label.textContent = `Prompt ${promptSectionCount}`;
  div.appendChild(label);

  const ta = document.createElement("textarea");
  ta.className = "prompt-box prompt-section-box";
  ta.rows = 4;
  ta.placeholder = "Describe the image... Type @ to insert [Image N] tag";
  ta.value = initialText;
  ta.addEventListener("input", (e) => onPromptInput(e, ta));
  ta.addEventListener("keydown", (e) => onPromptKeydown(e, ta));
  div.appendChild(ta);

  container.appendChild(div);
  updateRemovePromptBtn();
}

function setupFixedPromptMention() {
  const fp = document.getElementById("fixedPrompt");
  if (fp) {
    fp.addEventListener("input", (e) => onPromptInput(e, fp));
    fp.addEventListener("keydown", (e) => onPromptKeydown(e, fp));
  }
}

function removePromptSection() {
  const container = document.getElementById("promptSections");
  if (container.children.length <= 1) return;
  container.removeChild(container.lastElementChild);
  promptSectionCount = container.children.length;
  container.querySelectorAll(".field-label").forEach((lbl, i) => lbl.textContent = `Prompt ${i + 1}`);
  updateRemovePromptBtn();
  saveSettings();
}

function updateRemovePromptBtn() {
  const btn = document.getElementById("removePromptBtn");
  const count = document.getElementById("promptSections").children.length;
  btn.disabled = count < 2;
  btn.style.opacity = count < 2 ? "0.4" : "1";
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
// @Mention System for [Image N] tags
// ==========================================
function onPromptInput(e, textarea) {
  const pos = textarea.selectionStart;
  if (pos > 0 && textarea.value[pos - 1] === "@" && refCount > 0) {
    showMentionMenu(textarea, pos);
  } else if (mentionMenu) {
    const before = textarea.value.substring(0, pos);
    if (before.lastIndexOf("@") === -1) closeMentionMenu();
  }
}

function onPromptKeydown(e, textarea) {
  // Tab navigation between prompt boxes
  if (e.key === "Tab" && !mentionMenu) {
    e.preventDefault();
    const boxes = [...document.querySelectorAll(".prompt-section-box")];
    const fp = document.getElementById("fixedPrompt");
    const all = fp ? [fp, ...boxes] : boxes;
    const idx = all.indexOf(textarea);
    const next = e.shiftKey ? (idx - 1 + all.length) % all.length : (idx + 1) % all.length;
    all[next]?.focus();
    return;
  }
  if (!mentionMenu) return;
  if (e.key === "ArrowDown") { e.preventDefault(); navigateMention(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); navigateMention(-1); }
  else if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); insertMention(); }
  else if (e.key === "Escape") { e.preventDefault(); closeMentionMenu(); }
}

function showMentionMenu(textarea, cursorPos) {
  closeMentionMenu();
  if (refCount <= 0) return;
  mentionTarget = { textarea, cursorPos };
  mentionMenu = document.createElement("div");
  mentionMenu.className = "mention-menu";
  mentionMenu.dataset.selected = "0";

  for (let i = 0; i < refCount; i++) {
    const btn = document.createElement("button");
    btn.className = "mention-item" + (i === 0 ? " active" : "");
    btn.textContent = `Image ${i + 1}`;
    btn.dataset.index = i;
    btn.addEventListener("mousedown", (e) => {
      e.preventDefault();
      mentionMenu.dataset.selected = String(i);
      insertMention();
    });
    mentionMenu.appendChild(btn);
  }

  const rect = textarea.getBoundingClientRect();
  mentionMenu.style.left = `${rect.left + 20}px`;
  mentionMenu.style.bottom = `${window.innerHeight - rect.top + 4}px`;
  mentionMenu.style.top = "auto";
  document.body.appendChild(mentionMenu);
}

function navigateMention(dir) {
  if (!mentionMenu) return;
  const items = mentionMenu.querySelectorAll(".mention-item");
  let sel = parseInt(mentionMenu.dataset.selected || "0");
  items[sel]?.classList.remove("active");
  sel = (sel + dir + items.length) % items.length;
  items[sel]?.classList.add("active");
  mentionMenu.dataset.selected = String(sel);
}

function insertMention() {
  if (!mentionMenu || !mentionTarget) return;
  const sel = parseInt(mentionMenu.dataset.selected || "0");
  const tag = `[Image ${sel + 1}]`;
  const ta = mentionTarget.textarea;
  const pos = mentionTarget.cursorPos;
  const before = ta.value.substring(0, pos - 1);
  const after = ta.value.substring(pos);
  ta.value = before + tag + " " + after;
  ta.selectionStart = ta.selectionEnd = pos - 1 + tag.length + 1;
  ta.focus();
  closeMentionMenu();
}

function closeMentionMenu() {
  if (mentionMenu?.parentNode) mentionMenu.parentNode.removeChild(mentionMenu);
  mentionMenu = null;
  mentionTarget = null;
}

document.addEventListener("mousedown", (e) => {
  if (mentionMenu && !mentionMenu.contains(e.target)) closeMentionMenu();
});

// ==========================================
// Naming Controls
// ==========================================
function updateNamingControls() {
  const enabled = document.getElementById("namingSwitch").checked;
  const grid = document.getElementById("namingGrid");
  grid.classList.toggle("disabled", !enabled);
}

// ==========================================
// Reference Images
// ==========================================
async function refreshRefs() {
  const d = await api("/api/refs");
  refCount = d.count || 0;
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

    // [Image N] label
    const label = document.createElement("div");
    label.className = "ref-label";
    label.textContent = `[Image ${i + 1}]`;
    cell.appendChild(label);

    const img = document.createElement("img");
    img.src = `/api/refs/thumb/${i}?t=${Date.now()}`;
    img.alt = `ref ${i + 1}`;
    cell.appendChild(img);

    const actions = document.createElement("div");
    actions.className = "ref-actions";

    // Pin button
    const pinBtn = document.createElement("button");
    pinBtn.className = `ref-btn pin${ref.pinned ? " pinned" : ""}`;
    pinBtn.textContent = ref.pinned ? "Pinned" : "Pin";
    pinBtn.title = ref.pinned ? "Unpin" : "Pin";
    pinBtn.addEventListener("click", () => togglePin(i));
    actions.appendChild(pinBtn);

    // Change button
    const changeBtn = document.createElement("button");
    changeBtn.className = "ref-btn change";
    changeBtn.textContent = "Change";
    changeBtn.title = "Replace this image";
    changeBtn.addEventListener("click", () => replaceRef(i));
    actions.appendChild(changeBtn);

    // Remove button
    const removeBtn = document.createElement("button");
    removeBtn.className = "ref-btn";
    removeBtn.textContent = "\u2715";
    removeBtn.title = "Remove";
    removeBtn.addEventListener("click", () => removeRef(i));
    actions.appendChild(removeBtn);

    cell.appendChild(actions);
    grid.appendChild(cell);
  });
}

async function browseRefImages() {
  const d = await api("/api/browse-files", { method: "POST" });
  if (d.ok && d.added > 0) { refreshRefs(); showToast(`Added ${d.added} image(s)`, "success"); }
}

async function replaceRef(idx) {
  // Use file browse to replace a single ref
  const d = await api("/api/browse-replace-ref", { method: "POST", body: { index: idx } });
  if (d.ok) { refreshRefs(); showToast("Reference replaced", "success"); }
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
  if (d.ok) { refreshRefs(); showToast(d.message, "success"); }
  else showToast(d.message || "No image in clipboard. Try Ctrl+V.", "warn");
}

function onRefDragEnter(e) { e.preventDefault(); document.getElementById("refArea").classList.add("dragover"); }
function onRefDragLeave(e) { document.getElementById("refArea").classList.remove("dragover"); }
async function onRefDrop(e) {
  e.preventDefault();
  document.getElementById("refArea").classList.remove("dragover");
  const files = e.dataTransfer?.files;
  if (!files || files.length === 0) return;
  const form = new FormData();
  for (const f of files) {
    const ext = f.name.split(".").pop().toLowerCase();
    if (["png", "jpg", "jpeg", "webp", "bmp"].includes(ext)) form.append("files", f);
  }
  if ([...form.entries()].length === 0) { showToast("No supported images", "warn"); return; }
  const d = await api("/api/refs/upload", { method: "POST", body: form });
  if (d.added > 0) { refreshRefs(); showToast(`Added ${d.added} image(s)`, "success"); }
}

// ==========================================
// Gallery
// ==========================================
async function refreshGallery() {
  const d = await api("/api/gallery");
  if (!d.items) return;
  const grid = document.getElementById("galleryGrid");
  const empty = document.getElementById("emptyState");
  const search = document.getElementById("gallerySearch").value.toLowerCase();

  grid.querySelectorAll(".card").forEach(c => c.remove());

  let items = d.items;
  if (favoritesOnly) items = items.filter(it => it.favorite);
  if (search) items = items.filter(it =>
    (it.prompt || "").toLowerCase().includes(search) || (it.filename || "").toLowerCase().includes(search)
  );

  allGalleryPaths = items.map(it => it.filepath);

  if (items.length === 0 && !grid.querySelector(".skeleton")) {
    if (empty) empty.style.display = "block";
  } else {
    if (empty) empty.style.display = "none";
  }

  document.getElementById("countBadge").textContent = `${d.count} images`;

  items.forEach(item => {
    const card = document.createElement("div");
    card.className = "card" + (selectedPaths.includes(item.filepath) ? " selected" : "");
    card.dataset.path = item.filepath;

    // Image
    const img = document.createElement("img");
    img.className = "card-img";
    img.src = `/api/gallery/thumb?path=${encodeURIComponent(item.filepath)}&size=${getThumbSize()}`;
    img.loading = "lazy";
    img.addEventListener("click", () => openViewer(item.filepath));
    card.appendChild(img);

    // Body
    const body = document.createElement("div");
    body.className = "card-body";

    const fname = document.createElement("div");
    fname.className = "card-filename";
    fname.title = item.filename;
    fname.textContent = item.filename;
    body.appendChild(fname);

    const meta = document.createElement("div");
    meta.className = "card-meta";
    const apiBadgeClass = item.api_used === "vertex" ? "vertex" : "studio";
    const apiLabel = item.api_used === "vertex" ? "V" : "S";
    meta.innerHTML = `${item.elapsed_sec}s <span class="api-badge ${apiBadgeClass}">${apiLabel}</span>`
      + (item.resolution ? ` &bull; ${item.resolution}` : "")
      + (item.aspect ? ` &bull; ${item.aspect}` : "");
    body.appendChild(meta);

    // Actions row
    const actions = document.createElement("div");
    actions.className = "card-actions";

    const makeBtn = (text, cls, handler) => {
      const b = document.createElement("button");
      b.className = "card-btn " + cls;
      b.innerHTML = text;
      b.addEventListener("click", (e) => { e.stopPropagation(); handler(); });
      return b;
    };

    const favBtn = makeBtn("&#9733;", "fav" + (item.favorite ? " active" : ""), () => toggleFav(item.filepath, favBtn));
    actions.appendChild(favBtn);
    actions.appendChild(makeBtn("Ref", "ref-btn-card", () => useAsRef(item.filepath)));
    actions.appendChild(makeBtn("Explorer", "explorer-btn", () => openInExplorer(item.filepath)));
    actions.appendChild(makeBtn("Prompt", "prompt-btn", () => showPromptPopup(item.prompt)));
    actions.appendChild(makeBtn("Load", "load-btn", () => loadSetup(item.filepath)));
    actions.appendChild(makeBtn("Copy", "copy-btn", () => copyToClipboard(item.filepath)));
    actions.appendChild(makeBtn("Del", "del", () => deleteImage(item.filepath)));
    body.appendChild(actions);
    card.appendChild(body);

    // Click: select with Ctrl/Shift support
    card.addEventListener("click", (e) => {
      if (e.target.closest(".card-actions") || e.target.tagName === "IMG") return;
      selectCard(item.filepath, e);
    });
    grid.appendChild(card);
  });
}

function getThumbSize() {
  if (galleryColumns <= 1) return 920;
  if (galleryColumns <= 2) return 560;
  if (galleryColumns <= 4) return 320;
  return 180;
}

function filterGallery() { refreshGallery(); }

function toggleFavFilter() {
  favoritesOnly = !favoritesOnly;
  document.getElementById("favFilterBtn").classList.toggle("active", favoritesOnly);
  refreshGallery();
}

function setColumns(n) {
  galleryColumns = n;
  updateColumnsUI();
  refreshGallery();
  saveSettings();
}

function updateColumnsUI() {
  document.getElementById("galleryGrid").className = `gallery-grid cols-${galleryColumns}`;
  document.querySelectorAll(".layout-btn").forEach(b => {
    b.classList.toggle("active", parseInt(b.dataset.cols) === galleryColumns);
  });
}

// ==========================================
// Gallery Selection (Ctrl+Click, Shift+Click)
// ==========================================
function selectCard(filepath, e) {
  if (e?.shiftKey && selectionAnchor) {
    // Shift+Click: range select
    const startIdx = allGalleryPaths.indexOf(selectionAnchor);
    const endIdx = allGalleryPaths.indexOf(filepath);
    if (startIdx >= 0 && endIdx >= 0) {
      const lo = Math.min(startIdx, endIdx);
      const hi = Math.max(startIdx, endIdx);
      selectedPaths = allGalleryPaths.slice(lo, hi + 1);
    }
  } else if (e?.ctrlKey) {
    // Ctrl+Click: toggle single
    if (selectedPaths.includes(filepath)) {
      selectedPaths = selectedPaths.filter(p => p !== filepath);
    } else {
      selectedPaths.push(filepath);
    }
    selectionAnchor = filepath;
  } else {
    // Normal click: single select
    selectedPaths = [filepath];
    selectionAnchor = filepath;
  }
  updateSelectionUI();
}

function selectAll() {
  selectedPaths = [...allGalleryPaths];
  updateSelectionUI();
}

function updateSelectionUI() {
  document.querySelectorAll(".card").forEach(c => {
    c.classList.toggle("selected", selectedPaths.includes(c.dataset.path));
  });
}

// ==========================================
// Gallery Actions
// ==========================================
async function deleteImage(filepath) {
  // Check if favorited (matching original: cannot delete favorited)
  const card = document.querySelector(`.card[data-path="${CSS.escape(filepath)}"]`);
  const favBtn = card?.querySelector(".card-btn.fav.active");
  if (favBtn) {
    showToast("Unfavorite first before deleting", "warn");
    return;
  }
  if (!confirm("Delete this image?")) return;
  const d = await api("/api/gallery/delete", { method: "POST", body: { paths: [filepath] } });
  if (d.deleted > 0) {
    selectedPaths = selectedPaths.filter(p => p !== filepath);
    refreshGallery();
    showToast("Deleted", "success");
  } else if (d.errors?.length) {
    showToast(d.errors[0], "error");
  }
}

async function deleteSelected() {
  if (selectedPaths.length === 0) return;
  // Check if any are favorited
  for (const p of selectedPaths) {
    const card = document.querySelector(`.card[data-path="${CSS.escape(p)}"]`);
    if (card?.querySelector(".card-btn.fav.active")) {
      showToast("Some images are favorited. Unfavorite them first.", "warn");
      return;
    }
  }
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
  if (d.ok) { refreshRefs(); showToast("Added as reference", "success"); }
}

async function openInExplorer(filepath) {
  await api("/api/gallery/open-explorer", { method: "POST", body: { filepath } });
}

async function loadSetup(filepath) {
  const d = await api("/api/gallery/load-setup", { method: "POST", body: { filepath } });
  if (d.ok) { await loadSettings(); refreshRefs(); showToast("Loaded saved setup", "success"); }
  else showToast(d.error || "Failed", "error");
}

async function copyToClipboard(filepath) {
  const d = await api("/api/copy-to-clipboard", { method: "POST", body: { filepath } });
  showToast(d.ok ? "Copied to clipboard" : (d.error || "Failed"), d.ok ? "success" : "error");
}

function showPromptPopup(prompt) {
  const text = prompt || "(no prompt)";
  const w = window.open("", "_blank", "width=560,height=360");
  if (w) {
    w.document.write(`<!DOCTYPE html><html><head><title>Prompt</title><style>
body{background:#2C2C2E;color:#F5F5F7;font-family:'Malgun Gothic',sans-serif;padding:16px;}
pre{white-space:pre-wrap;word-break:break-all;font-size:11px;line-height:1.6;background:#1C1C1E;padding:12px;border-radius:10px;border:1px solid #48484A;}
button{margin-top:10px;padding:8px 20px;background:#D4A574;border:none;border-radius:8px;cursor:pointer;font-weight:bold;color:#1C1C1E;font-size:11px;}
button:hover{background:#C4956A;}
h3{font-size:13px;margin-bottom:8px;}
</style></head><body><h3>Prompt</h3><pre>${text.replace(/</g, "&lt;")}</pre>
<button onclick="navigator.clipboard.writeText(document.querySelector('pre').textContent);this.textContent='Copied!'">Copy</button></body></html>`);
  }
}

// ==========================================
// Generation
// ==========================================
async function generate() {
  await saveSettings();
  const d = await api("/api/generate", { method: "POST" });
  if (!d.ok) { showToast(d.error || "Cannot generate", "error"); return; }
  showToast(`Generating ${d.count} image(s)...`, "success");
  updateGenUI(true);

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
  isGenerating = generating;
  document.getElementById("genBtn").disabled = generating;
  document.getElementById("topGenBtn").disabled = generating;
  document.getElementById("stopBtn").disabled = !generating;
  document.getElementById("genBtn").textContent = generating ? "Generating..." : "Generate";
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
      document.getElementById("progressLabel").textContent = `Done  ok ${ev.done || 0}  fail ${ev.failed || 0}`;
      document.getElementById("progressFill").style.width = (ev.done || 0) > 0 ? "100%" : "0%";
      document.getElementById("statusLabel").textContent = `Completed  ${ev.done || 0} image(s) saved`;
    }
  }
}

function updateProgress(current, total) {
  if (total > 0) document.getElementById("progressFill").style.width = `${(current / total) * 100}%`;
  document.getElementById("progressLabel").textContent = `${current}/${total}`;
  document.getElementById("statusLabel").textContent = `Generating ${current}/${total}...`;
}

async function pollLogs() {
  const d = await api("/api/logs");
  if (!d.logs) return;
  const box = document.getElementById("logBox");
  box.textContent = d.logs.join("\n");
  box.scrollTop = box.scrollHeight;
}

async function refreshApiStatus() {
  const d = await api("/api/status");
  if (!d.vertex) return;
  document.getElementById("vertexDot").className = "dot " + d.vertex;
  document.getElementById("studioDot").className = "dot " + d.studio;
  if (d.is_generating && !isGenerating) updateGenUI(true);
  if (!d.is_generating && isGenerating) { updateGenUI(false); refreshGallery(); }
}

// ==========================================
// Folder / Project
// ==========================================
async function browseFolder() {
  const d = await api("/api/browse-folder", { method: "POST" });
  if (d.ok) { document.getElementById("folderInput").value = d.folder; showToast("Folder set", "success"); }
}

async function saveProject() {
  await saveSettings();
  const d = await api("/api/project/save", { method: "POST", body: {} });
  showToast(d.ok ? "Project saved" : (d.error || "Save failed"), d.ok ? "success" : "error");
}

async function loadProject() {
  const d = await api("/api/browse-project", { method: "POST" });
  if (d.ok) { await loadSettings(); refreshGallery(); refreshRefs(); showToast("Project loaded", "success"); }
}

// ==========================================
// Recent Projects Picker (on startup)
// ==========================================
async function checkRecentProjects() {
  // Only show if gallery is empty
  if (allGalleryPaths.length > 0) return;
  const d = await api("/api/project/recent");
  if (!d.projects || d.projects.length === 0) return;
  showProjectsModal(d.projects);
}

function showProjectsModal(projects) {
  const modal = document.getElementById("projectsModal");
  const list = document.getElementById("projectsList");
  list.innerHTML = "";

  projects.forEach(p => {
    const card = document.createElement("div");
    card.className = "project-card";
    card.addEventListener("click", () => { loadProjectByPath(p.filepath); closeProjectsModal(); });

    // Preview
    const preview = document.createElement("div");
    preview.className = "project-preview";
    if (p.preview_path) {
      const img = document.createElement("img");
      img.src = `/api/gallery/thumb?path=${encodeURIComponent(p.preview_path)}&size=128`;
      preview.appendChild(img);
    } else {
      const noP = document.createElement("div");
      noP.className = "no-preview";
      noP.textContent = "No preview";
      preview.appendChild(noP);
    }
    card.appendChild(preview);

    // Info
    const info = document.createElement("div");
    info.className = "project-info";
    const name = document.createElement("div");
    name.className = "project-name";
    name.textContent = p.name;
    info.appendChild(name);

    const meta = document.createElement("div");
    meta.className = "project-meta";
    const relTime = formatRelativeTime(p.modified_at);
    meta.textContent = `${relTime} \u2022 ${p.image_count} image(s)`;
    info.appendChild(meta);

    const prompt = document.createElement("div");
    prompt.className = "project-prompt";
    const pText = (p.prompt || "").replace(/\n/g, " ").trim() || "No prompt saved";
    prompt.textContent = pText.length > 84 ? pText.substring(0, 81) + "..." : pText;
    info.appendChild(prompt);

    card.appendChild(info);
    list.appendChild(card);
  });

  modal.classList.remove("hidden");
}

function closeProjectsModal() {
  document.getElementById("projectsModal").classList.add("hidden");
}

async function loadProjectByPath(filepath) {
  const d = await api("/api/project/load", { method: "POST", body: { filepath } });
  if (d.ok) { await loadSettings(); refreshGallery(); refreshRefs(); showToast("Project loaded", "success"); }
}

async function loadProjectFromBrowser() {
  closeProjectsModal();
  await loadProject();
}

function formatRelativeTime(timestamp) {
  if (!timestamp) return "Unknown";
  const delta = Math.max(0, Math.floor(Date.now() / 1000 - timestamp));
  if (delta < 60) return "Just now";
  const min = Math.floor(delta / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
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
// Image Viewer (persistent navigation)
// ==========================================
function openViewer(filepath) {
  const modal = document.getElementById("viewerModal");
  const canvas = document.getElementById("viewerCanvas");
  modal.classList.remove("hidden");
  viewerPath = filepath;

  const img = new window.Image();
  img.onload = () => {
    viewerState = { img, scale: 1, offsetX: 0, offsetY: 0, dragging: false, lastX: 0, lastY: 0 };
    fitViewer();
  };
  img.src = `/api/gallery/image?path=${encodeURIComponent(filepath)}`;
  document.getElementById("viewerTitle").textContent = filepath.split(/[/\\]/).pop();

  // Load prompt for footer
  fetch("/api/gallery").then(r => r.json()).then(d => {
    const item = (d.items || []).find(it => it.filepath === filepath);
    document.getElementById("viewerPrompt").textContent = item ? item.prompt : "";
  });

  // Bind canvas events (only once)
  if (!canvas._bound) {
    canvas.onmousedown = (e) => {
      if (viewerState) { viewerState.dragging = true; viewerState.lastX = e.clientX; viewerState.lastY = e.clientY; canvas.style.cursor = "grabbing"; }
    };
    canvas.onmousemove = (e) => {
      if (!viewerState?.dragging) return;
      viewerState.offsetX += e.clientX - viewerState.lastX;
      viewerState.offsetY += e.clientY - viewerState.lastY;
      viewerState.lastX = e.clientX; viewerState.lastY = e.clientY;
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
    canvas._bound = true;
  }
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
  ctx.drawImage(viewerState.img, viewerState.offsetX, viewerState.offsetY,
    viewerState.img.width * viewerState.scale, viewerState.img.height * viewerState.scale);
}

function closeViewer() {
  document.getElementById("viewerModal").classList.add("hidden");
  viewerState = null;
  viewerPath = null;
}

function navigateViewer(step) {
  if (!viewerPath && !document.getElementById("viewerModal").classList.contains("hidden")) return;
  if (!viewerPath) return;
  const paths = allGalleryPaths;
  if (paths.length === 0) return;
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
  toastTimer = setTimeout(() => t.classList.add("hidden"), 2000);
}

// ==========================================
// Auto-save settings on change
// ==========================================
["aspectSelect", "resolutionSelect", "countSelect", "namingSwitch",
 "namingPrefix", "namingDelimiter", "namingIndexPrefix", "namingPadding"].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener("change", () => saveSettings());
});
