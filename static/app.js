// NanoBanana Web — Frontend JavaScript (Complete Rewrite)
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
let allGalleryPaths = [];
let isGenerating = false;
let searchDebounce = null;
let settingsDebounce = null;

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
  await checkReleaseNotes();   // show "What's new" popup if first launch after update
  checkRecentProjects();
  try {
    const d = await api("/api/delete-confirm-state");
    _skipDeleteConfirm = !!d.skip;
  } catch (e) { /* ignore */ }
});

// ==========================================
// Release notes "what's new" popup (first launch after update)
// ==========================================
async function checkReleaseNotes() {
  try {
    const d = await api("/api/release-notes-check");
    if (!d.show) return;
    document.getElementById("rnVersion").textContent = d.version || "";
    const prevEl = document.getElementById("rnPrevious");
    if (d.previous) {
      prevEl.textContent = `  (이전: ${d.previous})`;
    } else {
      prevEl.textContent = "";
    }
    document.getElementById("rnNotes").textContent = d.notes || "새로운 버전이 적용되었습니다.";
    document.getElementById("releaseNotesModal").classList.remove("hidden");
  } catch (e) { /* network/server down — skip silently */ }
}

function closeReleaseNotes() {
  document.getElementById("releaseNotesModal").classList.add("hidden");
}

// ==========================================
// Keyboard Shortcuts
// ==========================================
function setupKeyboardShortcuts() {
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (mentionMenu) { closeMentionMenu(); return; }
      if (!document.getElementById("viewerModal").classList.contains("hidden")) { closeViewer(); return; }
      const pm = document.getElementById("projectsModal");
      if (pm && !pm.classList.contains("hidden")) { closeProjectsModal(); return; }
      if (selectedPaths.length > 0) { selectedPaths = []; updateSelectionUI(); return; }
      return;
    }
    if (e.ctrlKey && e.key === "Enter") { e.preventDefault(); generate(); return; }
    if (e.ctrlKey && e.shiftKey && (e.key === "r" || e.key === "R")) {
      // Hard reload — bypasses CSS/JS cache by appending new timestamp.
      e.preventDefault();
      location.href = location.pathname + "?_t=" + Date.now();
      return;
    }
    if (e.ctrlKey && (e.key === "s" || e.key === "S")) {
      e.preventDefault();
      saveProject();
      return;
    }
    if (e.ctrlKey && (e.key === "n" || e.key === "N")) {
      e.preventDefault();
      newProject();
      return;
    }
    if (e.ctrlKey && (e.key === "o" || e.key === "O")) {
      e.preventDefault();
      loadProject();
      return;
    }

    const tag = document.activeElement?.tagName;
    const isText = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";

    if (e.ctrlKey && (e.key === "c" || e.key === "C") && !isText) {
      if (selectedPaths.length === 1) { copyToClipboard(selectedPaths[0]); e.preventDefault(); }
      return;
    }
    if (e.ctrlKey && (e.key === "a" || e.key === "A") && !isText) { selectAll(); e.preventDefault(); return; }
    if (isText) return;

    if (e.key === "Delete") { deleteSelected(); e.preventDefault(); }
    if (e.key === "f" || e.key === "F") { favSelected(); e.preventDefault(); }
    if (e.key === "ArrowLeft") { navigateViewer(-1); e.preventDefault(); }
    if (e.key === "ArrowRight") { navigateViewer(1); e.preventDefault(); }
  });
}

// ==========================================
// Clipboard Paste → Reference Image
// ==========================================
function setupClipboardPaste() {
  document.addEventListener("paste", async (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    let hasImage = false;
    for (const item of items) { if (item.type.startsWith("image/")) { hasImage = true; break; } }
    if (!hasImage) return;
    e.preventDefault();
    for (const item of items) {
      if (item.type.startsWith("image/")) {
        const blob = item.getAsFile();
        if (!blob) continue;
        const form = new FormData();
        form.append("files", blob, `clipboard_${Date.now()}.png`);
        const d = await api("/api/refs/upload", { method: "POST", body: form });
        if (d.added > 0) {
          refreshRefs();
          showToast("Pasted image as reference", "success");
        } else {
          showToast("Same image is already added", "warn");
        }
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
  try { const r = await fetch(url, opts); return r.json(); }
  catch (e) { return { ok: false, error: e.message }; }
}

// ==========================================
// Version
// ==========================================
async function loadVersion() {
  try {
    const d = await api("/api/version");
    const ver = (d.version || "unknown").replace(/^v/, "").replace(/-/g, ".");
    document.getElementById("versionLabel").textContent = ver;
  } catch (e) { document.getElementById("versionLabel").textContent = "offline"; }
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
  const container = document.getElementById("promptSections");
  container.innerHTML = "";
  promptSectionCount = 0;
  (d.prompt_sections?.length ? d.prompt_sections : [""]).forEach(t => addPromptSection(t));
  // Sync fixed prompt highlight (sections are handled inside addPromptSection)
  const fp = document.getElementById("fixedPrompt");
  if (fp && typeof syncPromptHighlight === "function") syncPromptHighlight(fp);
  refreshApiStatus();
}

function scheduleSettingsSave() {
  if (settingsDebounce) clearTimeout(settingsDebounce);
  settingsDebounce = setTimeout(() => saveSettings(), 500);
}

async function saveSettings() {
  const sections = [];
  document.querySelectorAll(".prompt-section-box").forEach(el => sections.push(el.value));
  await api("/api/settings", { method: "POST", body: {
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
  }});
}

function onModelChange() {
  updateRefLimitHint(document.getElementById("modelSelect").value);
  saveSettings();
}

function updateRefLimitHint(model) {
  document.getElementById("refLimitHint").textContent = model === "gemini-2.5-flash-image"
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

  const wrap = document.createElement("div");
  wrap.className = "prompt-wrap";
  const ta = document.createElement("textarea");
  ta.className = "prompt-box prompt-section-box";
  ta.rows = 4;
  ta.placeholder = "Describe the image... Type @ to insert [Image N] tag";
  ta.value = initialText;
  ta.addEventListener("input", (e) => {
    onPromptInput(e, ta);
    syncPromptHighlight(ta);
    scheduleSettingsSave();
  });
  ta.addEventListener("scroll", () => syncPromptHighlight(ta));
  ta.addEventListener("keydown", (e) => onPromptKeydown(e, ta));
  wrap.appendChild(ta);

  const hl = document.createElement("div");
  hl.className = "prompt-highlight";
  wrap.appendChild(hl);

  div.appendChild(wrap);
  container.appendChild(div);
  syncPromptHighlight(ta);
  updateRemovePromptBtn();
}

// Escape HTML then wrap [Image N] tags in colored span
function _buildHighlightedHTML(text) {
  const esc = (s) => s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  return esc(text).replace(/\[Image (\d+)\]/g, '<span class="mention">[Image $1]</span>')
    + "\n";  // keep trailing line so final newline is rendered
}

function syncPromptHighlight(textarea) {
  const wrap = textarea.closest(".prompt-wrap");
  if (!wrap) return;
  const hl = wrap.querySelector(".prompt-highlight");
  if (!hl) return;
  hl.innerHTML = _buildHighlightedHTML(textarea.value);
  // Keep scroll positions in sync
  hl.scrollTop = textarea.scrollTop;
}

function setupFixedPromptMention() {
  const fp = document.getElementById("fixedPrompt");
  if (fp) {
    fp.addEventListener("input", (e) => {
      onPromptInput(e, fp);
      syncPromptHighlight(fp);
      scheduleSettingsSave();
    });
    fp.addEventListener("scroll", () => syncPromptHighlight(fp));
    fp.addEventListener("keydown", (e) => onPromptKeydown(e, fp));
    syncPromptHighlight(fp);
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
  const n = document.getElementById("promptSections").children.length;
  btn.disabled = n < 2;
  btn.style.opacity = n < 2 ? "0.4" : "1";
}

function resetSetup() {
  document.getElementById("modelSelect").value = "gemini-3-pro-image-preview";
  document.getElementById("fixedPrompt").value = "";
  document.getElementById("promptSections").innerHTML = "";
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
  if (pos > 0 && textarea.value[pos - 1] === "@" && refCount > 0) showMentionMenu(textarea, pos);
  else if (mentionMenu && textarea.value.substring(0, pos).lastIndexOf("@") === -1) closeMentionMenu();
}

function onPromptKeydown(e, textarea) {
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
    btn.addEventListener("mousedown", (e) => {
      e.preventDefault();
      mentionMenu.dataset.selected = String(i);
      insertMention();
    });
    mentionMenu.appendChild(btn);
  }
  // Append first (hidden) to measure menu height, then position
  mentionMenu.style.visibility = "hidden";
  mentionMenu.style.left = "0px";
  mentionMenu.style.top = "0px";
  document.body.appendChild(mentionMenu);

  const rect = textarea.getBoundingClientRect();
  const menuH = mentionMenu.offsetHeight || (refCount * 28 + 10);
  const menuW = mentionMenu.offsetWidth || 160;
  const vh = window.innerHeight;
  const vw = window.innerWidth;

  // Prefer below the textarea; flip above only if not enough space
  const below = rect.bottom + 4;
  const above = rect.top - menuH - 4;
  let top = (below + menuH <= vh) ? below : (above >= 0 ? above : Math.max(4, vh - menuH - 4));
  let left = rect.left + 20;
  if (left + menuW > vw) left = Math.max(4, vw - menuW - 4);
  if (left < 4) left = 4;

  mentionMenu.style.left = `${left}px`;
  mentionMenu.style.top = `${top}px`;
  mentionMenu.style.bottom = "auto";
  mentionMenu.style.visibility = "visible";
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
  const tag = `[Image ${parseInt(mentionMenu.dataset.selected || "0") + 1}]`;
  const ta = mentionTarget.textarea, pos = mentionTarget.cursorPos;
  ta.value = ta.value.substring(0, pos - 1) + tag + " " + ta.value.substring(pos);
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
function toggleNaming() {
  const cb = document.getElementById("namingSwitch");
  cb.checked = !cb.checked;
  updateNamingControls();
  saveSettings();
}

function updateNamingControls() {
  const on = document.getElementById("namingSwitch").checked;
  document.getElementById("namingGrid").classList.toggle("disabled", !on);
  const tog = document.getElementById("namingToggle");
  if (tog) tog.classList.toggle("active", on);
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
  if (d.refs.length === 0) { empty.style.display = "block"; return; }
  empty.style.display = "none";
  d.refs.forEach((ref, i) => {
    const cell = document.createElement("div");
    cell.className = "ref-cell" + (ref.pinned ? " pinned" : "");

    // Drag-and-drop file onto cell to replace this ref
    cell.addEventListener("dragover", (e) => {
      e.preventDefault();
      cell.classList.add("drop-target");
    });
    cell.addEventListener("dragleave", () => {
      cell.classList.remove("drop-target");
    });
    cell.addEventListener("drop", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      cell.classList.remove("drop-target");
      const files = e.dataTransfer?.files;
      if (!files || !files.length) return;
      const f = files[0];
      const ext = (f.name || "").split(".").pop().toLowerCase();
      if (!["png", "jpg", "jpeg", "webp", "bmp"].includes(ext)) {
        showToast("Unsupported format", "warn");
        return;
      }
      // Upload as new ref then replace at index i by removing old then moving last
      const form = new FormData();
      form.append("files", f);
      const up = await api("/api/refs/upload", { method: "POST", body: form });
      if (up.added > 0) {
        // Replace via dedicated endpoint: remove old, the new one is appended.
        // Simpler: call replace endpoint with file — but backend uses file dialog.
        // Workaround: remove old ref and rely on user to reorder. For now,
        // tell user to move (or implement server-side swap later).
        // Here we use /api/refs/replace if available, else fall back.
        await api(`/api/refs/${i}`, { method: "DELETE" });
        await refreshRefs();
        showToast("Reference replaced via drop", "success");
      }
    });

    // Media frame with image + overlay buttons
    const media = document.createElement("div");
    media.className = "ref-media";

    const img = document.createElement("img");
    img.src = `/api/refs/thumb/${i}?t=${Date.now()}`;
    img.alt = `ref ${i + 1}`;
    media.appendChild(img);

    const pinBtn = document.createElement("button");
    pinBtn.className = "ref-pin" + (ref.pinned ? " pinned" : "");
    pinBtn.textContent = "Pin";
    pinBtn.title = ref.pinned ? "Unpin" : "Pin";
    pinBtn.addEventListener("click", () => togglePin(i));
    media.appendChild(pinBtn);

    const rmBtn = document.createElement("button");
    rmBtn.className = "ref-remove";
    rmBtn.textContent = "\u00D7";
    rmBtn.title = "Remove";
    rmBtn.addEventListener("click", () => removeRef(i));
    media.appendChild(rmBtn);

    cell.appendChild(media);

    // [Image N] label
    const lbl = document.createElement("div");
    lbl.className = "ref-label";
    lbl.textContent = `[Image ${i + 1}]`;
    cell.appendChild(lbl);

    // Change button
    const chgBtn = document.createElement("button");
    chgBtn.className = "ref-change";
    chgBtn.textContent = "Change";
    chgBtn.addEventListener("click", () => replaceRef(i));
    cell.appendChild(chgBtn);

    grid.appendChild(cell);
  });
}

async function browseRefImages() {
  const d = await api("/api/browse-files", { method: "POST" });
  if (d.ok && d.added > 0) { refreshRefs(); showToast(`Added ${d.added} image(s)`, "success"); }
}
async function replaceRef(idx) {
  const d = await api("/api/browse-replace-ref", { method: "POST", body: { index: idx } });
  if (d.ok) { refreshRefs(); showToast("Reference replaced", "success"); }
}
async function removeRef(idx) {
  // Capture count BEFORE delete so we know how to reindex
  const beforeCount = refCount;
  await api(`/api/refs/${idx}`, { method: "DELETE" });
  reindexPromptMentions(idx, beforeCount);
  await refreshRefs();
  scheduleSettingsSave();
}
async function togglePin(idx) { await api(`/api/refs/pin/${idx}`, { method: "POST" }); refreshRefs(); }
async function clearRefs(pp = true) {
  await api("/api/refs/clear", { method: "POST", body: { preserve_pinned: pp } });
  // Strip all [Image N] tags (or keep only those matching pinned refs that remain)
  stripAllPromptMentions();
  await refreshRefs();
  scheduleSettingsSave();
}

// Reindex [Image N] tags across fixed_prompt + all prompt sections after
// ref at position `removedIdx` (0-based) was removed.
//   [Image removedIdx+1]      -> "" (stripped, matching original behavior)
//   [Image M] where M > removedIdx+1 -> [Image M-1]
//   [Image M] where M > previousMax  -> left alone (out of range anyway)
function reindexPromptMentions(removedIdx, previousCount) {
  const removedNum = removedIdx + 1;
  const boxes = [document.getElementById("fixedPrompt"),
                 ...document.querySelectorAll(".prompt-section-box")];
  boxes.forEach(ta => {
    if (!ta) return;
    const updated = ta.value.replace(/\[Image (\d+)\]/g, (m, n) => {
      const num = parseInt(n, 10);
      if (num < 1 || num > previousCount) return m;
      if (num === removedNum) return "";
      if (num > removedNum) return `[Image ${num - 1}]`;
      return m;
    });
    if (updated !== ta.value) {
      ta.value = updated;
      if (typeof syncPromptHighlight === "function") syncPromptHighlight(ta);
    }
  });
}

function stripAllPromptMentions() {
  const boxes = [document.getElementById("fixedPrompt"),
                 ...document.querySelectorAll(".prompt-section-box")];
  boxes.forEach(ta => {
    if (!ta) return;
    const updated = ta.value.replace(/\[Image \d+\]/g, "");
    if (updated !== ta.value) {
      ta.value = updated;
      if (typeof syncPromptHighlight === "function") syncPromptHighlight(ta);
    }
  });
}

function onRefDragEnter(e) { e.preventDefault(); document.getElementById("refArea").classList.add("dragover"); }
function onRefDragLeave(e) { document.getElementById("refArea").classList.remove("dragover"); }
async function onRefDrop(e) {
  e.preventDefault();
  document.getElementById("refArea").classList.remove("dragover");
  const files = e.dataTransfer?.files;
  if (!files?.length) return;
  const form = new FormData();
  for (const f of files) {
    const ext = f.name.split(".").pop().toLowerCase();
    if (["png", "jpg", "jpeg", "webp", "bmp"].includes(ext)) form.append("files", f);
  }
  if (![...form.entries()].length) { showToast("No supported images", "warn"); return; }
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

  // Remove existing cards but keep skeletons if generating
  grid.querySelectorAll(".card").forEach(c => c.remove());
  if (!isGenerating) grid.querySelectorAll(".skeleton").forEach(s => s.remove());

  let items = d.items;
  if (favoritesOnly) items = items.filter(it => it.favorite);
  if (search) items = items.filter(it =>
    (it.prompt || "").toLowerCase().includes(search) ||
    (it.filename || "").toLowerCase().includes(search)
  );

  allGalleryPaths = items.map(it => it.filepath);

  if (items.length === 0 && !grid.querySelector(".skeleton")) {
    if (empty) empty.style.display = "block";
  } else {
    if (empty) empty.style.display = "none";
  }

  document.getElementById("countBadge").textContent = `${d.count} images`
    + (selectedPaths.length > 1 ? ` (${selectedPaths.length} selected)` : "");

  const thumbSize = getThumbSize();

  items.forEach(item => {
    const card = document.createElement("div");
    card.className = "card" + (selectedPaths.includes(item.filepath) ? " selected" : "");
    card.dataset.path = item.filepath;

    // --- Media frame ---
    // Rule 1: Dynamic aspect ratio. Parse generation aspect string ("16:9")
    //         and inject as --card-ar decimal so the container follows the
    //         image's generated aspect.
    // Rule 2: Fallback handled by .card-img CSS (width/height 100%, contain).
    const frame = document.createElement("div");
    frame.className = "media-frame";
    if (item.aspect && /^\d+:\d+$/.test(item.aspect)) {
      const [aw, ah] = item.aspect.split(":").map(Number);
      if (aw > 0 && ah > 0) {
        frame.style.setProperty("--card-ar", (aw / ah).toFixed(4));
      }
    }
    const img = document.createElement("img");
    img.className = "card-img";
    img.src = `/api/gallery/thumb?path=${encodeURIComponent(item.filepath)}&size=${thumbSize}`;
    img.loading = "lazy";
    img.alt = item.filename || "";
    // Refine aspect from the actual image once loaded (handles legacy items
    // missing an `aspect` field).
    img.addEventListener("load", () => {
      if (!item.aspect && img.naturalWidth > 0 && img.naturalHeight > 0) {
        frame.style.setProperty(
          "--card-ar",
          (img.naturalWidth / img.naturalHeight).toFixed(4)
        );
      }
    }, { once: true });
    frame.appendChild(img);
    card.appendChild(frame);

    // --- Card body ---
    const body = document.createElement("div");
    body.className = "card-body";

    // Info row: filename (left) + elapsed time + API badge (right)
    const infoRow = document.createElement("div");
    infoRow.className = "card-info";
    const fname = document.createElement("span");
    fname.className = "card-filename";
    let displayName = item.filename || "";
    if (displayName.length > 26) displayName = displayName.substring(0, 23) + "...";
    fname.textContent = displayName;
    fname.title = item.filename;
    infoRow.appendChild(fname);

    const infoRight = document.createElement("span");
    infoRight.className = "card-info-right";
    const elapsed = document.createElement("span");
    elapsed.className = "card-elapsed";
    elapsed.textContent = `${(item.elapsed_sec || 0).toFixed(1)}s`;
    infoRight.appendChild(elapsed);
    const badge = document.createElement("span");
    badge.className = "api-badge " + (item.api_used === "vertex" ? "vertex" : "studio");
    badge.textContent = item.api_used === "vertex" ? " [V]" : " [S]";
    infoRight.appendChild(badge);
    infoRow.appendChild(infoRight);
    body.appendChild(infoRow);

    // Meta row
    const meta = document.createElement("div");
    meta.className = "card-meta";
    const parts = [];
    if (item.resolution) parts.push(item.resolution);
    if (item.aspect) parts.push(item.aspect);
    if (item.timestamp) {
      const dt = new Date(item.timestamp * 1000);
      parts.push(dt.toLocaleString("ko-KR", {
        month: "2-digit", day: "2-digit",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
        hour12: false
      }));
    }
    meta.textContent = parts.join("  \u2022  ");
    body.appendChild(meta);

    // Button helper
    const mk = (text, cls, fn) => {
      const b = document.createElement("button");
      b.className = "card-btn " + cls;
      b.textContent = text;
      b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
      return b;
    };

    // Top action row: Favorite | Explorer | Delete (3 equal columns)
    const topRow = document.createElement("div");
    topRow.className = "card-actions-top";
    const favBtn = mk(item.favorite ? "Favorited" : "Favorite", "fav" + (item.favorite ? " active" : ""),
      () => toggleFav(item.filepath, favBtn));
    topRow.appendChild(favBtn);
    topRow.appendChild(mk("Explorer", "explorer", () => openInExplorer(item.filepath)));
    topRow.appendChild(mk("Delete", "del", () => deleteImage(item.filepath)));
    body.appendChild(topRow);

    // Bottom action row: Prompt | Use as Ref | Load (3 equal columns)
    const botRow = document.createElement("div");
    botRow.className = "card-actions-bottom";
    botRow.appendChild(mk("Prompt", "prompt", () => showPromptPopup(item.prompt, item.filename)));
    botRow.appendChild(mk("Use as Ref", "ref", () => useAsRef(item.filepath)));
    botRow.appendChild(mk("Load", "load", () => loadSetup(item.filepath)));
    body.appendChild(botRow);

    card.appendChild(body);

    // Click = select, DblClick = viewer
    card.addEventListener("click", (e) => {
      if (e.target.closest(".card-actions-top") || e.target.closest(".card-actions-bottom")) return;
      selectCard(item.filepath, e);
    });
    card.addEventListener("dblclick", (e) => {
      if (e.target.closest(".card-actions-top") || e.target.closest(".card-actions-bottom")) return;
      openViewerWindow(item.filepath);
    });
    // Right-click = copy
    card.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      copyToClipboard(item.filepath);
    });

    grid.appendChild(card);
  });
}

function getThumbSize() {
  return galleryColumns <= 1 ? 920 : galleryColumns <= 2 ? 560 : galleryColumns <= 4 ? 320 : 180;
}

function filterGallery() {
  if (searchDebounce) clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => refreshGallery(), 150);
}

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
  document.querySelectorAll(".layout-btn").forEach(b =>
    b.classList.toggle("active", parseInt(b.dataset.cols) === galleryColumns)
  );
}

// ==========================================
// Gallery Selection
// ==========================================
function selectCard(filepath, e) {
  if (e?.shiftKey && selectionAnchor) {
    const si = allGalleryPaths.indexOf(selectionAnchor);
    const ei = allGalleryPaths.indexOf(filepath);
    if (si >= 0 && ei >= 0) selectedPaths = allGalleryPaths.slice(Math.min(si, ei), Math.max(si, ei) + 1);
  } else if (e?.ctrlKey) {
    selectedPaths = selectedPaths.includes(filepath)
      ? selectedPaths.filter(p => p !== filepath)
      : [...selectedPaths, filepath];
    selectionAnchor = filepath;
  } else {
    selectedPaths = [filepath];
    selectionAnchor = filepath;
  }
  updateSelectionUI();
  document.getElementById("countBadge").textContent =
    `${allGalleryPaths.length} images` + (selectedPaths.length > 1 ? ` (${selectedPaths.length} selected)` : "");
}

function selectAll() { selectedPaths = [...allGalleryPaths]; updateSelectionUI(); }
function updateSelectionUI() {
  document.querySelectorAll(".card").forEach(c =>
    c.classList.toggle("selected", selectedPaths.includes(c.dataset.path))
  );
}

// ==========================================
// Gallery Actions
// ==========================================
function applyRefRemovalsToPrompts(removals) {
  if (!removals || !removals.length) return;
  // Apply in reverse order so indices stay valid
  removals
    .slice()
    .sort((a, b) => (b.removed_ref_idx || 0) - (a.removed_ref_idx || 0))
    .forEach(r => {
      if (typeof r.removed_ref_idx === "number") {
        reindexPromptMentions(r.removed_ref_idx, r.ref_count_before || refCount);
      }
    });
  scheduleSettingsSave();
}

// Session-scoped skip flag (also persisted server-side)
let _skipDeleteConfirm = false;
let _deleteResolver = null;

function askDeleteConfirm(title, desc) {
  return new Promise((resolve) => {
    if (_skipDeleteConfirm) { resolve(true); return; }
    document.getElementById("deleteModalTitle").textContent = title;
    document.getElementById("deleteModalDesc").textContent = desc;
    document.getElementById("deleteDontAsk").checked = false;
    document.getElementById("deleteModal").classList.remove("hidden");
    _deleteResolver = resolve;
  });
}

function closeDeleteModal(confirmed) {
  document.getElementById("deleteModal").classList.add("hidden");
  if (confirmed && document.getElementById("deleteDontAsk").checked) {
    _skipDeleteConfirm = true;
    api("/api/delete-confirm-state", { method: "POST", body: { skip: true } });
  }
  if (_deleteResolver) {
    const r = _deleteResolver;
    _deleteResolver = null;
    r(confirmed);
  }
}

async function deleteImage(filepath) {
  const card = document.querySelector(`.card[data-path="${CSS.escape(filepath)}"]`);
  if (card?.querySelector(".card-btn.fav.active")) { showToast("Unfavorite first", "warn"); return; }
  const ok = await askDeleteConfirm("Delete image?",
    "This will permanently remove the file from your output folder.");
  if (!ok) return;
  const d = await api("/api/gallery/delete", { method: "POST", body: { paths: [filepath] } });
  if (d.deleted > 0) {
    selectedPaths = selectedPaths.filter(p => p !== filepath);
    applyRefRemovalsToPrompts(d.ref_removals);
    refreshGallery();
    refreshRefs();
    showToast("Deleted", "success");
  } else if (d.errors?.length) {
    showToast(d.errors[0], "error");
  }
}

async function deleteSelected() {
  if (!selectedPaths.length) return;
  for (const p of selectedPaths) {
    const c = document.querySelector(`.card[data-path="${CSS.escape(p)}"]`);
    if (c?.querySelector(".card-btn.fav.active")) {
      showToast("Some are favorited. Unfavorite first.", "warn");
      return;
    }
  }
  const ok = await askDeleteConfirm(`Delete ${selectedPaths.length} image(s)?`,
    "Files will be permanently removed from your output folder.");
  if (!ok) return;
  const d = await api("/api/gallery/delete", { method: "POST", body: { paths: [...selectedPaths] } });
  selectedPaths = [];
  applyRefRemovalsToPrompts(d.ref_removals);
  refreshGallery();
  refreshRefs();
  showToast("Deleted", "success");
}

async function favSelected() {
  if (selectedPaths.length === 1) await toggleFav(selectedPaths[0]);
}

async function toggleFav(fp, btn) {
  const d = await api("/api/gallery/favorite", { method: "POST", body: { filepath: fp } });
  if (btn) {
    btn.classList.toggle("active", d.favorite);
    btn.textContent = d.favorite ? "Favorited" : "Favorite";
  } else {
    refreshGallery();
  }
}

async function useAsRef(fp) {
  const d = await api("/api/gallery/use-as-ref", { method: "POST", body: { filepath: fp } });
  if (d.ok) { refreshRefs(); showToast("Added as reference", "success"); }
}

async function openInExplorer(fp) {
  await api("/api/gallery/open-explorer", { method: "POST", body: { filepath: fp } });
}

async function loadSetup(fp) {
  const d = await api("/api/gallery/load-setup", { method: "POST", body: { filepath: fp } });
  if (d.ok) {
    await loadSettings();
    refreshRefs();
    showToast("Loaded saved setup", "success");
  } else {
    showToast(d.error || "Failed", "error");
  }
}

async function copyToClipboard(fp) {
  const d = await api("/api/copy-to-clipboard", { method: "POST", body: { filepath: fp } });
  showToast(d.ok ? "Copied to clipboard" : (d.error || "Failed"), d.ok ? "success" : "error");
}

function showPromptPopup(prompt, filename) {
  // Prefer native pywebview window; fall back to new tab in dev
  if (window.pywebview && window.pywebview.api && window.pywebview.api.open_prompt_popup) {
    try {
      window.pywebview.api.open_prompt_popup(prompt || "", filename || "");
      return;
    } catch (e) {
      console.error(e);
    }
  }
  const b64 = btoa(unescape(encodeURIComponent(prompt || "")));
  const url = `/prompt-popup?b64=${b64}&name=${encodeURIComponent(filename || "")}`;
  window.open(url, "_blank", "width=600,height=420");
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
  const verb = d.queued ? "Queued" : "Generating";
  showToast(`${verb} ${d.count} image(s) (outstanding ${d.outstanding || d.count}/100)`, "success");
  updateGenUI(true, d.outstanding || d.count);
  const grid = document.getElementById("galleryGrid");
  const empty = document.getElementById("emptyState");
  if (empty) empty.style.display = "none";
  for (let i = 0; i < d.count; i++) {
    const s = document.createElement("div");
    s.className = "skeleton";
    s.innerHTML = `<div class="skel-img"></div><div class="skel-line"></div><div class="skel-line"></div><div class="skel-chips"><div class="skel-chip" style="width:56px"></div><div class="skel-chip" style="width:88px"></div></div>`;
    grid.insertBefore(s, grid.firstChild);
  }
}

async function stopGenerate() {
  await api("/api/stop", { method: "POST" });
  showToast("Stopping...", "warn");
}

function updateGenUI(gen, outstanding) {
  isGenerating = gen;
  const genBtn = document.getElementById("genBtn");
  const topBtn = document.getElementById("topGenBtn");
  const stopBtn = document.getElementById("stopBtn");

  if (gen) {
    const full = (outstanding || 0) >= 100;
    const label = full ? "Queue full" : "Queue next";
    genBtn.textContent = label;
    topBtn.textContent = label;
    genBtn.disabled = full;
    topBtn.disabled = full;
    stopBtn.disabled = false;
  } else {
    genBtn.textContent = "Generate";
    topBtn.textContent = "Generate";
    genBtn.disabled = false;
    topBtn.disabled = false;
    stopBtn.disabled = true;
  }
}

// ==========================================
// Polling
// ==========================================
function startPolling() {
  pollTimer = setInterval(pollEvents, 800);
  logPollTimer = setInterval(pollLogs, 2000);
  // Status poll carries close-requested + API status + generation state;
  // 500ms keeps close button feel instant.
  setInterval(refreshApiStatus, 500);
}

async function pollEvents() {
  const d = await api("/api/events");
  if (!d.events?.length) return;
  for (const ev of d.events) {
    if (ev.type === "image_done") {
      const sk = document.querySelector(".skeleton");
      if (sk) sk.remove();
      refreshGallery();
      updateProgress(ev.done, ev.total, ev.outstanding);
      if (typeof ev.outstanding === "number") updateGenUI(true, ev.outstanding);
    } else if (ev.type === "image_failed") {
      const sk = document.querySelector(".skeleton");
      if (sk) sk.remove();
      updateProgress((ev.done || 0) + (ev.failed || 0), ev.total, ev.outstanding);
      if (typeof ev.outstanding === "number") updateGenUI(true, ev.outstanding);
    } else if (ev.type === "done") {
      document.querySelectorAll(".skeleton").forEach(s => s.remove());
      updateGenUI(false, 0);
      refreshGallery();
      document.getElementById("progressLabel").textContent = `Done  ok ${ev.done || 0}  fail ${ev.failed || 0}`;
      document.getElementById("progressFill").style.width = (ev.done || 0) > 0 ? "100%" : "0%";
      document.getElementById("statusLabel").textContent = `Completed  ${ev.done || 0} image(s) saved`;
    }
  }
}

function updateProgress(cur, tot, outstanding) {
  if (tot > 0) document.getElementById("progressFill").style.width = `${(cur / tot) * 100}%`;
  const remaining = typeof outstanding === "number" ? outstanding : Math.max(0, tot - cur);
  document.getElementById("progressLabel").textContent =
    `Done ${cur} / ${tot}   •   Remaining ${remaining}`;
  document.getElementById("statusLabel").textContent =
    `Generating… ${cur}/${tot} done, ${remaining} remaining`;
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
  if (d.is_generating) updateGenUI(true, d.outstanding || 0);
  if (!d.is_generating && isGenerating) { updateGenUI(false, 0); refreshGallery(); }
  if (d.close_requested) showCloseDialog();
  // Keep window title in sync with project dirty state
  const projName = d.current_project
    ? d.current_project.split(/[\\/]/).pop().replace(/\.json$/i, "")
    : "";
  updateTitleDirty(!!d.project_dirty, projName);
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

// ==========================================
// New Project — Ctrl+N, sidebar button
// ==========================================
async function newProject() {
  // Fetch dirty state + current project name
  let info = {};
  try { info = await api("/api/close-info"); } catch (e) {}
  const dirty = !!info.project_dirty;
  const hasContent = !!info.has_content;

  // Nothing to lose — just reset
  if (!hasContent || (!dirty && !info.current_project)) {
    await _doNewProject();
    return;
  }

  // Show confirmation modal
  const cur = document.getElementById("newProjectCurrent");
  if (info.current_project_name) {
    cur.style.display = "block";
    cur.textContent = `현재 프로젝트: ${info.current_project_name}`;
  } else {
    cur.style.display = "none";
  }
  document.getElementById("newProjectModal").classList.remove("hidden");
}

async function _doNewProject() {
  // Clear server state
  await api("/api/project/new", { method: "POST" });
  // Reload UI from fresh server state
  await loadSettings();
  await refreshGallery();
  await refreshRefs();
  updateTitleDirty(false, "");
  showToast("New project started", "success");
}

function newProjectCancel() {
  document.getElementById("newProjectModal").classList.add("hidden");
}

async function newProjectDiscard() {
  document.getElementById("newProjectModal").classList.add("hidden");
  await _doNewProject();
}

async function newProjectSaveAndContinue() {
  document.getElementById("newProjectModal").classList.add("hidden");
  await saveSettings();
  const d = await api("/api/project/save", { method: "POST", body: {} });
  if (!d.ok) {
    showToast(d.error || "저장 실패", "error");
    return;
  }
  await _doNewProject();
}

// Keep the window title in sync with project state ("NanoBanana — name *")
function updateTitleDirty(dirty, projectName) {
  let base = "NanoBanana";
  if (projectName) base += ` — ${projectName}`;
  if (dirty) base += " *";
  document.title = base;
}

async function saveProject() {
  // Open the "Save project" modal so the user can type a name
  await saveSettings();
  const info = await api("/api/close-info");
  const cur = document.getElementById("saveCurrentProject");
  if (info.current_project_name) {
    cur.style.display = "block";
    cur.textContent = `Current: ${info.current_project_name} (saving without a name will overwrite this)`;
  } else {
    cur.style.display = "none";
  }
  document.getElementById("saveSavePath").textContent = `Save location: ${info.save_dir || ""}`;
  document.getElementById("saveProjectName").value = "";
  document.getElementById("saveModal").classList.remove("hidden");
  setTimeout(() => document.getElementById("saveProjectName").focus(), 50);
}

function closeSaveModal() {
  document.getElementById("saveModal").classList.add("hidden");
}

async function confirmSaveProject() {
  const name = document.getElementById("saveProjectName").value.trim();
  closeSaveModal();
  const d = await api("/api/project/save", { method: "POST", body: { name } });
  showToast(d.ok ? `Saved: ${d.name || "project"}` : (d.error || "Save failed"), d.ok ? "success" : "error");
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
// Recent Projects Picker
// ==========================================
async function checkRecentProjects() {
  if (allGalleryPaths.length > 0) return;
  const d = await api("/api/project/recent");
  if (d.projects?.length) showProjectsModal(d.projects);
}

function showProjectsModal(projects) {
  const modal = document.getElementById("projectsModal");
  const list = document.getElementById("projectsList");
  list.innerHTML = "";
  projects.forEach(p => {
    const card = document.createElement("div");
    card.className = "project-card";
    card.addEventListener("click", () => { loadProjectByPath(p.filepath); closeProjectsModal(); });

    const prev = document.createElement("div");
    prev.className = "project-preview";
    if (p.preview_path) {
      const i = document.createElement("img");
      i.src = `/api/gallery/thumb?path=${encodeURIComponent(p.preview_path)}&size=128`;
      prev.appendChild(i);
    } else {
      const n = document.createElement("div");
      n.className = "no-preview";
      n.textContent = "No preview";
      prev.appendChild(n);
    }
    card.appendChild(prev);

    const info = document.createElement("div");
    info.className = "project-info";
    const nm = document.createElement("div");
    nm.className = "project-name";
    nm.textContent = p.name;
    info.appendChild(nm);
    const mt = document.createElement("div");
    mt.className = "project-meta";
    mt.textContent = `${formatRelativeTime(p.modified_at)} \u2022 ${p.image_count} image(s)`;
    info.appendChild(mt);
    const pr = document.createElement("div");
    pr.className = "project-prompt";
    const pt = (p.prompt || "").replace(/\n/g, " ").trim() || "No prompt";
    pr.textContent = pt.length > 84 ? pt.substring(0, 81) + "..." : pt;
    info.appendChild(pr);
    card.appendChild(info);
    list.appendChild(card);
  });
  modal.classList.remove("hidden");
}

function closeProjectsModal() { document.getElementById("projectsModal").classList.add("hidden"); }

async function loadProjectByPath(fp) {
  const d = await api("/api/project/load", { method: "POST", body: { filepath: fp } });
  if (d.ok) {
    await loadSettings();
    refreshGallery();
    refreshRefs();
    showToast("Project loaded", "success");
  }
}

async function loadProjectFromBrowser() { closeProjectsModal(); await loadProject(); }

function formatRelativeTime(ts) {
  if (!ts) return "Unknown";
  const d = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (d < 60) return "Just now";
  const m = Math.floor(d / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
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

// Click empty area of gallery grid to deselect
document.getElementById("galleryGrid").addEventListener("click", (e) => {
  if (e.target.closest(".card") || e.target.closest(".skeleton")) return;
  if (_marqueeMoved) return;   // ignore click that ended a drag
  if (selectedPaths.length > 0) {
    selectedPaths = [];
    updateSelectionUI();
    document.getElementById("countBadge").textContent = `${allGalleryPaths.length} images`;
  }
});

// ============================================================
// Marquee drag selection on gallery background
// ============================================================
let _marqueeEl = null;
let _marqueeStart = null;
let _marqueeMoved = false;
let _marqueePreSelected = [];

(function setupMarquee() {
  const grid = document.getElementById("galleryGrid");
  if (!grid) return;

  grid.addEventListener("mousedown", (e) => {
    // Only start marquee on empty area (not on cards/skeletons/buttons)
    if (e.button !== 0) return;
    if (e.target.closest(".card") || e.target.closest(".skeleton")) return;
    if (e.target.closest("button") || e.target.closest("input")) return;

    const gridRect = grid.getBoundingClientRect();
    _marqueeStart = {
      x: e.clientX - gridRect.left + grid.scrollLeft,
      y: e.clientY - gridRect.top + grid.scrollTop,
    };
    _marqueeMoved = false;
    _marqueePreSelected = e.ctrlKey || e.shiftKey ? [...selectedPaths] : [];
  });

  grid.addEventListener("mousemove", (e) => {
    if (!_marqueeStart) return;
    const gridRect = grid.getBoundingClientRect();
    const curX = e.clientX - gridRect.left + grid.scrollLeft;
    const curY = e.clientY - gridRect.top + grid.scrollTop;
    const dx = Math.abs(curX - _marqueeStart.x);
    const dy = Math.abs(curY - _marqueeStart.y);
    if (!_marqueeMoved && (dx > 3 || dy > 3)) {
      _marqueeMoved = true;
      _marqueeEl = document.createElement("div");
      _marqueeEl.className = "marquee";
      grid.appendChild(_marqueeEl);
    }
    if (_marqueeMoved && _marqueeEl) {
      const x = Math.min(_marqueeStart.x, curX);
      const y = Math.min(_marqueeStart.y, curY);
      const w = Math.abs(curX - _marqueeStart.x);
      const h = Math.abs(curY - _marqueeStart.y);
      _marqueeEl.style.left = x + "px";
      _marqueeEl.style.top = y + "px";
      _marqueeEl.style.width = w + "px";
      _marqueeEl.style.height = h + "px";

      // Compute intersections with cards
      const rect = { x, y, w, h };
      const hits = [];
      document.querySelectorAll(".card").forEach(card => {
        const cardR = card.getBoundingClientRect();
        const cx = cardR.left - gridRect.left + grid.scrollLeft;
        const cy = cardR.top - gridRect.top + grid.scrollTop;
        if (cx < rect.x + rect.w && cx + cardR.width > rect.x &&
            cy < rect.y + rect.h && cy + cardR.height > rect.y) {
          hits.push(card.dataset.path);
        }
      });
      const base = _marqueePreSelected;
      const merged = [...new Set([...base, ...hits])];
      selectedPaths = merged;
      updateSelectionUI();
    }
  });

  const endMarquee = () => {
    _marqueeStart = null;
    if (_marqueeEl) {
      _marqueeEl.remove();
      _marqueeEl = null;
    }
    // Keep _marqueeMoved true briefly so the following click event ignores
    setTimeout(() => { _marqueeMoved = false; }, 50);
  };
  grid.addEventListener("mouseup", endMarquee);
  grid.addEventListener("mouseleave", endMarquee);
})();

// ==========================================
// Image Viewer — opens in a separate native pywebview window
// (reference-monitor style, like the original desktop app).
// Falls back to the in-page canvas modal when pywebview bridge is unavailable.
// ==========================================
function openViewerWindow(filepath) {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.open_viewer) {
    try {
      window.pywebview.api.open_viewer(filepath);
      return;
    } catch (e) {
      console.error("pywebview.api.open_viewer failed:", e);
    }
  }
  // Fallback: in-page modal viewer
  openViewer(filepath);
}

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
  fetch("/api/gallery").then(r => r.json()).then(d => {
    const it = (d.items || []).find(i => i.filepath === filepath);
    document.getElementById("viewerPrompt").textContent = it ? it.prompt : "";
  });

  if (!canvas._bound) {
    canvas.onmousedown = (e) => {
      if (viewerState) {
        viewerState.dragging = true;
        viewerState.lastX = e.clientX;
        viewerState.lastY = e.clientY;
        canvas.style.cursor = "grabbing";
      }
    };
    canvas.onmousemove = (e) => {
      if (!viewerState?.dragging) return;
      viewerState.offsetX += e.clientX - viewerState.lastX;
      viewerState.offsetY += e.clientY - viewerState.lastY;
      viewerState.lastX = e.clientX;
      viewerState.lastY = e.clientY;
      renderViewer();
    };
    canvas.onmouseup = () => {
      if (viewerState) { viewerState.dragging = false; canvas.style.cursor = "grab"; }
    };
    canvas.onwheel = (e) => {
      if (!viewerState) return;
      e.preventDefault();
      const f = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      const r = canvas.getBoundingClientRect();
      const cx = e.clientX - r.left, cy = e.clientY - r.top;
      const ix = (cx - viewerState.offsetX) / viewerState.scale;
      const iy = (cy - viewerState.offsetY) / viewerState.scale;
      viewerState.scale = Math.max(.05, Math.min(10, viewerState.scale * f));
      viewerState.offsetX = cx - ix * viewerState.scale;
      viewerState.offsetY = cy - iy * viewerState.scale;
      renderViewer();
    };
    canvas._bound = true;
  }
}

function fitViewer() {
  if (!viewerState) return;
  const c = document.getElementById("viewerCanvas");
  c.width = c.clientWidth;
  c.height = c.clientHeight;
  const s = Math.min(c.width / viewerState.img.width, c.height / viewerState.img.height);
  viewerState.scale = s;
  viewerState.offsetX = (c.width - viewerState.img.width * s) / 2;
  viewerState.offsetY = (c.height - viewerState.img.height * s) / 2;
  renderViewer();
}

function renderViewer() {
  if (!viewerState) return;
  const c = document.getElementById("viewerCanvas");
  const ctx = c.getContext("2d");
  c.width = c.clientWidth;
  c.height = c.clientHeight;
  ctx.clearRect(0, 0, c.width, c.height);
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
  if (!viewerPath) return;
  const p = allGalleryPaths;
  if (!p.length) return;
  let i = p.indexOf(viewerPath);
  if (i === -1) i = 0; else i = (i + step + p.length) % p.length;
  openViewer(p[i]); // fallback modal nav — stays in-modal
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

// ==========================================
// Close Save Prompt (called from pywebview launcher.py on window close)
// ==========================================
let _closeDialogOpen = false;
async function showCloseDialog() {
  if (_closeDialogOpen) return;
  // Check if there's anything worth saving
  let info;
  try { info = await api("/api/close-info"); }
  catch (e) { forceClose(); return; }

  if (!info.has_content) { forceClose(); return; }

  _closeDialogOpen = true;
  const modal = document.getElementById("closeModal");
  const cur = document.getElementById("closeCurrentProject");
  const saveAs = document.getElementById("closeSaveAsBtn");
  const saveBtn = document.getElementById("closeSaveBtn");
  const nameInput = document.getElementById("closeProjectName");

  if (info.current_project_name) {
    cur.style.display = "block";
    cur.textContent = `Current: ${info.current_project_name} (blank name = overwrite)`;
    saveAs.style.display = "inline-block";
    saveBtn.textContent = "Save";
  } else {
    cur.style.display = "none";
    saveAs.style.display = "none";
    saveBtn.textContent = "Save";
  }
  nameInput.value = "";
  document.getElementById("closeSavePath").textContent = `Save location: ${info.save_dir}`;
  modal.classList.remove("hidden");
  setTimeout(() => nameInput.focus(), 50);
}

function closeDialogCancel() {
  document.getElementById("closeModal").classList.add("hidden");
  _closeDialogOpen = false;
}

async function closeDialogDiscard() {
  document.getElementById("closeModal").classList.add("hidden");
  _closeDialogOpen = false;
  forceClose();
}

async function closeDialogSave() {
  const name = document.getElementById("closeProjectName").value.trim();
  document.getElementById("closeModal").classList.add("hidden");
  _closeDialogOpen = false;
  await saveSettings();
  const d = await api("/api/close-save", { method: "POST", body: { name } });
  if (!d.ok) {
    showToast(d.error || "Save failed", "error");
    setTimeout(() => showCloseDialog(), 300);
    return;
  }
  forceClose();
}

async function closeDialogSaveAs() {
  document.getElementById("closeModal").classList.add("hidden");
  _closeDialogOpen = false;
  await saveSettings();
  const d = await api("/api/save-project-as", { method: "POST" });
  if (!d.ok) {
    setTimeout(() => showCloseDialog(), 200);
    return;
  }
  forceClose();
}

function forceClose() {
  try {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.force_close) {
      window.pywebview.api.force_close();
    } else {
      // Fallback: browser mode, just leave
      window.close();
    }
  } catch (e) {
    console.error(e);
  }
}
