// NanoBanana Web — Frontend JavaScript (Full Feature Parity + Polish)
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
  checkRecentProjects();
});

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
      // Deselect all on Escape
      if (selectedPaths.length > 0) { selectedPaths = []; updateSelectionUI(); return; }
      return;
    }
    if (e.ctrlKey && e.key === "Enter") { e.preventDefault(); generate(); return; }

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
  updateColumnsUI(); updateNamingControls(); updateRefLimitHint(d.model);
  const container = document.getElementById("promptSections");
  container.innerHTML = ""; promptSectionCount = 0;
  (d.prompt_sections?.length ? d.prompt_sections : [""]).forEach(t => addPromptSection(t));
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

function onModelChange() { updateRefLimitHint(document.getElementById("modelSelect").value); saveSettings(); }

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
  const ta = document.createElement("textarea");
  ta.className = "prompt-box prompt-section-box";
  ta.rows = 4;
  ta.placeholder = "Describe the image... Type @ to insert [Image N] tag";
  ta.value = initialText;
  ta.addEventListener("input", (e) => { onPromptInput(e, ta); scheduleSettingsSave(); });
  ta.addEventListener("keydown", (e) => onPromptKeydown(e, ta));
  div.appendChild(ta);
  container.appendChild(div);
  updateRemovePromptBtn();
}

function setupFixedPromptMention() {
  const fp = document.getElementById("fixedPrompt");
  if (fp) {
    fp.addEventListener("input", (e) => { onPromptInput(e, fp); scheduleSettingsSave(); });
    fp.addEventListener("keydown", (e) => onPromptKeydown(e, fp));
  }
}

function removePromptSection() {
  const container = document.getElementById("promptSections");
  if (container.children.length <= 1) return;
  container.removeChild(container.lastElementChild);
  promptSectionCount = container.children.length;
  container.querySelectorAll(".field-label").forEach((lbl, i) => lbl.textContent = `Prompt ${i + 1}`);
  updateRemovePromptBtn(); saveSettings();
}

function updateRemovePromptBtn() {
  const btn = document.getElementById("removePromptBtn");
  const n = document.getElementById("promptSections").children.length;
  btn.disabled = n < 2; btn.style.opacity = n < 2 ? "0.4" : "1";
}

function resetSetup() {
  document.getElementById("modelSelect").value = "gemini-3-pro-image-preview";
  document.getElementById("fixedPrompt").value = "";
  document.getElementById("promptSections").innerHTML = "";
  promptSectionCount = 0;
  addPromptSection("");
  clearRefs(false); saveSettings();
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
    all[next]?.focus(); return;
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
    btn.addEventListener("mousedown", (e) => { e.preventDefault(); mentionMenu.dataset.selected = String(i); insertMention(); });
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
  const tag = `[Image ${parseInt(mentionMenu.dataset.selected || "0") + 1}]`;
  const ta = mentionTarget.textarea, pos = mentionTarget.cursorPos;
  ta.value = ta.value.substring(0, pos - 1) + tag + " " + ta.value.substring(pos);
  ta.selectionStart = ta.selectionEnd = pos - 1 + tag.length + 1;
  ta.focus(); closeMentionMenu();
}

function closeMentionMenu() {
  if (mentionMenu?.parentNode) mentionMenu.parentNode.removeChild(mentionMenu);
  mentionMenu = null; mentionTarget = null;
}
document.addEventListener("mousedown", (e) => { if (mentionMenu && !mentionMenu.contains(e.target)) closeMentionMenu(); });

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
    const cell = document.createElement("div"); cell.className = "ref-cell";
    const lbl = document.createElement("div"); lbl.className = "ref-label"; lbl.textContent = `[Image ${i+1}]`;
    cell.appendChild(lbl);
    const img = document.createElement("img"); img.src = `/api/refs/thumb/${i}?t=${Date.now()}`; img.alt = `ref ${i+1}`;
    cell.appendChild(img);
    const acts = document.createElement("div"); acts.className = "ref-actions";
    const pinBtn = document.createElement("button");
    pinBtn.className = `ref-btn pin${ref.pinned ? " pinned" : ""}`;
    pinBtn.textContent = ref.pinned ? "Pinned" : "Pin";
    pinBtn.addEventListener("click", () => togglePin(i)); acts.appendChild(pinBtn);
    const chgBtn = document.createElement("button"); chgBtn.className = "ref-btn change";
    chgBtn.textContent = "Change"; chgBtn.addEventListener("click", () => replaceRef(i)); acts.appendChild(chgBtn);
    const rmBtn = document.createElement("button"); rmBtn.className = "ref-btn";
    rmBtn.textContent = "\u2715"; rmBtn.addEventListener("click", () => removeRef(i)); acts.appendChild(rmBtn);
    cell.appendChild(acts); grid.appendChild(cell);
  });
}

async function browseRefImages() { const d = await api("/api/browse-files",{method:"POST"}); if(d.ok&&d.added>0){refreshRefs();showToast(`Added ${d.added} image(s)`,"success");} }
async function replaceRef(idx) { const d = await api("/api/browse-replace-ref",{method:"POST",body:{index:idx}}); if(d.ok){refreshRefs();showToast("Reference replaced","success");} }
async function removeRef(idx) { await api(`/api/refs/${idx}`,{method:"DELETE"}); refreshRefs(); }
async function togglePin(idx) { await api(`/api/refs/pin/${idx}`,{method:"POST"}); refreshRefs(); }
async function clearRefs(pp=true) { await api("/api/refs/clear",{method:"POST",body:{preserve_pinned:pp}}); refreshRefs(); }
async function pasteClipboardRef() { const d=await api("/api/refs/paste",{method:"POST"}); if(d.ok){refreshRefs();showToast(d.message,"success");}else showToast(d.message||"No image. Try Ctrl+V.","warn"); }

function onRefDragEnter(e) { e.preventDefault(); document.getElementById("refArea").classList.add("dragover"); }
function onRefDragLeave(e) { document.getElementById("refArea").classList.remove("dragover"); }
async function onRefDrop(e) {
  e.preventDefault(); document.getElementById("refArea").classList.remove("dragover");
  const files = e.dataTransfer?.files; if (!files?.length) return;
  const form = new FormData();
  for (const f of files) { const ext = f.name.split(".").pop().toLowerCase(); if (["png","jpg","jpeg","webp","bmp"].includes(ext)) form.append("files", f); }
  if (![...form.entries()].length) { showToast("No supported images", "warn"); return; }
  const d = await api("/api/refs/upload", { method: "POST", body: form });
  if (d.added > 0) { refreshRefs(); showToast(`Added ${d.added} image(s)`, "success"); }
}

// ==========================================
// Gallery
// ==========================================
async function refreshGallery() {
  const d = await api("/api/gallery"); if (!d.items) return;
  const grid = document.getElementById("galleryGrid");
  const empty = document.getElementById("emptyState");
  const search = document.getElementById("gallerySearch").value.toLowerCase();

  grid.querySelectorAll(".card").forEach(c => c.remove());
  if (!isGenerating) grid.querySelectorAll(".skeleton").forEach(s => s.remove());

  let items = d.items;
  if (favoritesOnly) items = items.filter(it => it.favorite);
  if (search) items = items.filter(it => (it.prompt||"").toLowerCase().includes(search)||(it.filename||"").toLowerCase().includes(search));

  allGalleryPaths = items.map(it => it.filepath);
  if (items.length === 0 && !grid.querySelector(".skeleton")) { if(empty) empty.style.display = "block"; }
  else { if(empty) empty.style.display = "none"; }

  document.getElementById("countBadge").textContent = `${d.count} images`
    + (selectedPaths.length > 1 ? ` (${selectedPaths.length} selected)` : "");

  items.forEach(item => {
    const card = document.createElement("div");
    card.className = "card" + (selectedPaths.includes(item.filepath) ? " selected" : "");
    card.dataset.path = item.filepath;

    const img = document.createElement("img"); img.className = "card-img";
    img.src = `/api/gallery/thumb?path=${encodeURIComponent(item.filepath)}&size=${getThumbSize()}`;
    img.loading = "lazy";
    // Set aspect ratio from generation settings
    const ar = item.aspect || "16:9";
    const [aw, ah] = ar.split(":").map(Number);
    if (aw && ah) img.style.aspectRatio = `${aw}/${ah}`;
    card.appendChild(img);

    const body = document.createElement("div"); body.className = "card-body";
    const fname = document.createElement("div"); fname.className = "card-filename";
    fname.title = item.filename; fname.textContent = item.filename; body.appendChild(fname);

    const meta = document.createElement("div"); meta.className = "card-meta";
    const bc = item.api_used === "vertex" ? "vertex" : "studio";
    const bl = item.api_used === "vertex" ? "V" : "S";
    meta.innerHTML = `${item.elapsed_sec}s <span class="api-badge ${bc}">${bl}</span>`
      + (item.resolution ? ` &bull; ${item.resolution}` : "") + (item.aspect ? ` &bull; ${item.aspect}` : "");
    body.appendChild(meta);

    const acts = document.createElement("div"); acts.className = "card-actions";
    const mk = (text, cls, fn) => { const b = document.createElement("button"); b.className="card-btn "+cls; b.innerHTML=text; b.addEventListener("click",(e)=>{e.stopPropagation();fn();}); return b; };
    const favBtn = mk("&#9733;","fav"+(item.favorite?" active":""),()=>toggleFav(item.filepath,favBtn));
    acts.appendChild(favBtn);
    acts.appendChild(mk("Ref","ref-btn-card",()=>useAsRef(item.filepath)));
    acts.appendChild(mk("Explorer","explorer-btn",()=>openInExplorer(item.filepath)));
    acts.appendChild(mk("Prompt","prompt-btn",()=>showPromptPopup(item.prompt)));
    acts.appendChild(mk("Load","load-btn",()=>loadSetup(item.filepath)));
    acts.appendChild(mk("Copy","copy-btn",()=>copyToClipboard(item.filepath)));
    acts.appendChild(mk("Del","del",()=>deleteImage(item.filepath)));
    body.appendChild(acts); card.appendChild(body);

    // Single click = select, Double click = viewer
    card.addEventListener("click", (e) => {
      if (e.target.closest(".card-actions")) return;
      selectCard(item.filepath, e);
    });
    card.addEventListener("dblclick", (e) => {
      if (e.target.closest(".card-actions")) return;
      openViewer(item.filepath);
    });
    grid.appendChild(card);
  });
}

function getThumbSize() { return galleryColumns<=1?920:galleryColumns<=2?560:galleryColumns<=4?320:180; }

function filterGallery() {
  if (searchDebounce) clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => refreshGallery(), 150);
}

function toggleFavFilter() { favoritesOnly=!favoritesOnly; document.getElementById("favFilterBtn").classList.toggle("active",favoritesOnly); refreshGallery(); }
function setColumns(n) { galleryColumns=n; updateColumnsUI(); refreshGallery(); saveSettings(); }
function updateColumnsUI() {
  document.getElementById("galleryGrid").className=`gallery-grid cols-${galleryColumns}`;
  document.querySelectorAll(".layout-btn").forEach(b=>b.classList.toggle("active",parseInt(b.dataset.cols)===galleryColumns));
}

// ==========================================
// Gallery Selection
// ==========================================
function selectCard(filepath, e) {
  if (e?.shiftKey && selectionAnchor) {
    const si = allGalleryPaths.indexOf(selectionAnchor), ei = allGalleryPaths.indexOf(filepath);
    if (si >= 0 && ei >= 0) selectedPaths = allGalleryPaths.slice(Math.min(si,ei), Math.max(si,ei)+1);
  } else if (e?.ctrlKey) {
    selectedPaths = selectedPaths.includes(filepath) ? selectedPaths.filter(p=>p!==filepath) : [...selectedPaths, filepath];
    selectionAnchor = filepath;
  } else {
    selectedPaths = [filepath]; selectionAnchor = filepath;
  }
  updateSelectionUI();
  document.getElementById("countBadge").textContent =
    `${allGalleryPaths.length} images` + (selectedPaths.length > 1 ? ` (${selectedPaths.length} selected)` : "");
}

function selectAll() { selectedPaths=[...allGalleryPaths]; updateSelectionUI(); }
function updateSelectionUI() { document.querySelectorAll(".card").forEach(c=>c.classList.toggle("selected",selectedPaths.includes(c.dataset.path))); }

// ==========================================
// Gallery Actions
// ==========================================
async function deleteImage(filepath) {
  const card = document.querySelector(`.card[data-path="${CSS.escape(filepath)}"]`);
  if (card?.querySelector(".card-btn.fav.active")) { showToast("Unfavorite first","warn"); return; }
  if (!confirm("Delete this image?")) return;
  const d = await api("/api/gallery/delete",{method:"POST",body:{paths:[filepath]}});
  if (d.deleted>0) { selectedPaths=selectedPaths.filter(p=>p!==filepath); refreshGallery(); showToast("Deleted","success"); }
  else if (d.errors?.length) showToast(d.errors[0],"error");
}

async function deleteSelected() {
  if (!selectedPaths.length) return;
  for (const p of selectedPaths) {
    const c = document.querySelector(`.card[data-path="${CSS.escape(p)}"]`);
    if (c?.querySelector(".card-btn.fav.active")) { showToast("Some are favorited. Unfavorite first.","warn"); return; }
  }
  if (!confirm(`Delete ${selectedPaths.length} image(s)?`)) return;
  await api("/api/gallery/delete",{method:"POST",body:{paths:[...selectedPaths]}});
  selectedPaths=[]; refreshGallery(); showToast("Deleted","success");
}

async function favSelected() { if(selectedPaths.length===1) await toggleFav(selectedPaths[0]); }
async function toggleFav(fp,btn) { const d=await api("/api/gallery/favorite",{method:"POST",body:{filepath:fp}}); if(btn)btn.classList.toggle("active",d.favorite); else refreshGallery(); }
async function useAsRef(fp) { const d=await api("/api/gallery/use-as-ref",{method:"POST",body:{filepath:fp}}); if(d.ok){refreshRefs();showToast("Added as reference","success");} }
async function openInExplorer(fp) { await api("/api/gallery/open-explorer",{method:"POST",body:{filepath:fp}}); }
async function loadSetup(fp) { const d=await api("/api/gallery/load-setup",{method:"POST",body:{filepath:fp}}); if(d.ok){await loadSettings();refreshRefs();showToast("Loaded saved setup","success");}else showToast(d.error||"Failed","error"); }
async function copyToClipboard(fp) { const d=await api("/api/copy-to-clipboard",{method:"POST",body:{filepath:fp}}); showToast(d.ok?"Copied to clipboard":(d.error||"Failed"),d.ok?"success":"error"); }

function showPromptPopup(prompt) {
  const text = prompt || "(no prompt)";
  const w = window.open("","_blank","width=560,height=360");
  if (w) w.document.write(`<!DOCTYPE html><html><head><title>Prompt</title><style>body{background:#2C2C2E;color:#F5F5F7;font-family:'Malgun Gothic',sans-serif;padding:16px;}pre{white-space:pre-wrap;word-break:break-all;font-size:11px;line-height:1.6;background:#1C1C1E;padding:12px;border-radius:10px;border:1px solid #48484A;}button{margin-top:10px;padding:8px 20px;background:#D4A574;border:none;border-radius:8px;cursor:pointer;font-weight:bold;color:#1C1C1E;font-size:11px;}button:hover{background:#C4956A;}h3{font-size:13px;margin-bottom:8px;}</style></head><body><h3>Prompt</h3><pre>${text.replace(/</g,"&lt;")}</pre><button onclick="navigator.clipboard.writeText(document.querySelector('pre').textContent);this.textContent='Copied!'">Copy</button></body></html>`);
}

// ==========================================
// Generation
// ==========================================
async function generate() {
  await saveSettings();
  const d = await api("/api/generate",{method:"POST"});
  if (!d.ok) { showToast(d.error||"Cannot generate","error"); return; }
  showToast(`Generating ${d.count} image(s)...`,"success");
  updateGenUI(true);
  const grid = document.getElementById("galleryGrid");
  const empty = document.getElementById("emptyState");
  if (empty) empty.style.display = "none";
  for (let i=0;i<d.count;i++) {
    const s = document.createElement("div"); s.className="skeleton";
    s.innerHTML=`<div class="skel-img"></div><div class="skel-line"></div><div class="skel-line"></div><div class="skel-chips"><div class="skel-chip" style="width:56px"></div><div class="skel-chip" style="width:88px"></div></div>`;
    grid.insertBefore(s, grid.firstChild);
  }
}

async function stopGenerate() { await api("/api/stop",{method:"POST"}); showToast("Stopping...","warn"); }

function updateGenUI(gen) {
  isGenerating = gen;
  document.getElementById("genBtn").disabled = gen;
  document.getElementById("topGenBtn").disabled = gen;
  document.getElementById("stopBtn").disabled = !gen;
  document.getElementById("genBtn").textContent = gen ? "Generating..." : "Generate";
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
  if (!d.events?.length) return;
  for (const ev of d.events) {
    if (ev.type === "image_done") {
      const sk = document.querySelector(".skeleton"); if(sk) sk.remove();
      refreshGallery();
      updateProgress(ev.done, ev.total);
    } else if (ev.type === "image_failed") {
      const sk = document.querySelector(".skeleton"); if(sk) sk.remove();
      updateProgress((ev.done||0)+(ev.failed||0), ev.total);
    } else if (ev.type === "done") {
      document.querySelectorAll(".skeleton").forEach(s=>s.remove());
      updateGenUI(false); refreshGallery();
      document.getElementById("progressLabel").textContent = `Done  ok ${ev.done||0}  fail ${ev.failed||0}`;
      document.getElementById("progressFill").style.width = (ev.done||0)>0?"100%":"0%";
      document.getElementById("statusLabel").textContent = `Completed  ${ev.done||0} image(s) saved`;
    }
  }
}

function updateProgress(cur,tot) {
  if(tot>0) document.getElementById("progressFill").style.width=`${(cur/tot)*100}%`;
  document.getElementById("progressLabel").textContent=`${cur}/${tot}`;
  document.getElementById("statusLabel").textContent=`Generating ${cur}/${tot}...`;
}

async function pollLogs() {
  const d = await api("/api/logs"); if(!d.logs) return;
  const box = document.getElementById("logBox");
  box.textContent = d.logs.join("\n"); box.scrollTop = box.scrollHeight;
}

async function refreshApiStatus() {
  const d = await api("/api/status"); if(!d.vertex) return;
  document.getElementById("vertexDot").className = "dot "+d.vertex;
  document.getElementById("studioDot").className = "dot "+d.studio;
  if (d.is_generating && !isGenerating) updateGenUI(true);
  if (!d.is_generating && isGenerating) { updateGenUI(false); refreshGallery(); }
}

// ==========================================
// Folder / Project
// ==========================================
async function browseFolder() { const d=await api("/api/browse-folder",{method:"POST"}); if(d.ok){document.getElementById("folderInput").value=d.folder;showToast("Folder set","success");} }
async function saveProject() { await saveSettings(); const d=await api("/api/project/save",{method:"POST",body:{}}); showToast(d.ok?"Project saved":(d.error||"Save failed"),d.ok?"success":"error"); }
async function loadProject() { const d=await api("/api/browse-project",{method:"POST"}); if(d.ok){await loadSettings();refreshGallery();refreshRefs();showToast("Project loaded","success");} }

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
    const card = document.createElement("div"); card.className = "project-card";
    card.addEventListener("click", () => { loadProjectByPath(p.filepath); closeProjectsModal(); });
    const prev = document.createElement("div"); prev.className = "project-preview";
    if (p.preview_path) { const i=document.createElement("img"); i.src=`/api/gallery/thumb?path=${encodeURIComponent(p.preview_path)}&size=128`; prev.appendChild(i); }
    else { const n=document.createElement("div"); n.className="no-preview"; n.textContent="No preview"; prev.appendChild(n); }
    card.appendChild(prev);
    const info = document.createElement("div"); info.className = "project-info";
    const nm = document.createElement("div"); nm.className="project-name"; nm.textContent=p.name; info.appendChild(nm);
    const mt = document.createElement("div"); mt.className="project-meta"; mt.textContent=`${formatRelativeTime(p.modified_at)} \u2022 ${p.image_count} image(s)`; info.appendChild(mt);
    const pr = document.createElement("div"); pr.className="project-prompt";
    const pt = (p.prompt||"").replace(/\n/g," ").trim()||"No prompt"; pr.textContent=pt.length>84?pt.substring(0,81)+"...":pt;
    info.appendChild(pr); card.appendChild(info); list.appendChild(card);
  });
  modal.classList.remove("hidden");
}

function closeProjectsModal() { document.getElementById("projectsModal").classList.add("hidden"); }
async function loadProjectByPath(fp) { const d=await api("/api/project/load",{method:"POST",body:{filepath:fp}}); if(d.ok){await loadSettings();refreshGallery();refreshRefs();showToast("Project loaded","success");} }
async function loadProjectFromBrowser() { closeProjectsModal(); await loadProject(); }

function formatRelativeTime(ts) {
  if(!ts) return "Unknown";
  const d=Math.max(0,Math.floor(Date.now()/1000-ts));
  if(d<60) return "Just now"; const m=Math.floor(d/60);
  if(m<60) return `${m}m ago`; const h=Math.floor(m/60);
  if(h<24) return `${h}h ago`; return `${Math.floor(h/24)}d ago`;
}

// ==========================================
// Sidebar Toggle
// ==========================================
document.getElementById("toggleSidebar").addEventListener("click", () => {
  const sb=document.getElementById("sidebar"), btn=document.getElementById("toggleSidebar");
  sb.classList.toggle("hidden"); btn.textContent=sb.classList.contains("hidden")?"Show Panel":"Hide Panel";
});

// ==========================================
// Image Viewer
// ==========================================
function openViewer(filepath) {
  const modal=document.getElementById("viewerModal"), canvas=document.getElementById("viewerCanvas");
  modal.classList.remove("hidden"); viewerPath=filepath;
  const img=new window.Image();
  img.onload=()=>{ viewerState={img,scale:1,offsetX:0,offsetY:0,dragging:false,lastX:0,lastY:0}; fitViewer(); };
  img.src=`/api/gallery/image?path=${encodeURIComponent(filepath)}`;
  document.getElementById("viewerTitle").textContent=filepath.split(/[/\\]/).pop();
  fetch("/api/gallery").then(r=>r.json()).then(d=>{
    const it=(d.items||[]).find(i=>i.filepath===filepath);
    document.getElementById("viewerPrompt").textContent=it?it.prompt:"";
  });
  if (!canvas._bound) {
    canvas.onmousedown=(e)=>{if(viewerState){viewerState.dragging=true;viewerState.lastX=e.clientX;viewerState.lastY=e.clientY;canvas.style.cursor="grabbing";}};
    canvas.onmousemove=(e)=>{if(!viewerState?.dragging)return;viewerState.offsetX+=e.clientX-viewerState.lastX;viewerState.offsetY+=e.clientY-viewerState.lastY;viewerState.lastX=e.clientX;viewerState.lastY=e.clientY;renderViewer();};
    canvas.onmouseup=()=>{if(viewerState){viewerState.dragging=false;canvas.style.cursor="grab";}};
    canvas.onwheel=(e)=>{if(!viewerState)return;e.preventDefault();const f=e.deltaY<0?1.15:1/1.15;const r=canvas.getBoundingClientRect();const cx=e.clientX-r.left,cy=e.clientY-r.top;const ix=(cx-viewerState.offsetX)/viewerState.scale,iy=(cy-viewerState.offsetY)/viewerState.scale;viewerState.scale=Math.max(.05,Math.min(10,viewerState.scale*f));viewerState.offsetX=cx-ix*viewerState.scale;viewerState.offsetY=cy-iy*viewerState.scale;renderViewer();};
    canvas._bound=true;
  }
}

function fitViewer() {
  if(!viewerState) return; const c=document.getElementById("viewerCanvas");
  c.width=c.clientWidth; c.height=c.clientHeight;
  const s=Math.min(c.width/viewerState.img.width,c.height/viewerState.img.height);
  viewerState.scale=s; viewerState.offsetX=(c.width-viewerState.img.width*s)/2; viewerState.offsetY=(c.height-viewerState.img.height*s)/2; renderViewer();
}

function renderViewer() {
  if(!viewerState) return; const c=document.getElementById("viewerCanvas"), ctx=c.getContext("2d");
  c.width=c.clientWidth; c.height=c.clientHeight; ctx.clearRect(0,0,c.width,c.height);
  ctx.imageSmoothingEnabled=true; ctx.imageSmoothingQuality="high";
  ctx.drawImage(viewerState.img, viewerState.offsetX, viewerState.offsetY, viewerState.img.width*viewerState.scale, viewerState.img.height*viewerState.scale);
}

function closeViewer() { document.getElementById("viewerModal").classList.add("hidden"); viewerState=null; viewerPath=null; }

function navigateViewer(step) {
  if(!viewerPath) return; const p=allGalleryPaths; if(!p.length) return;
  let i=p.indexOf(viewerPath); if(i===-1)i=0; else i=(i+step+p.length)%p.length;
  openViewer(p[i]);
}

window.addEventListener("resize",()=>{if(viewerState) fitViewer();});

// ==========================================
// Toast
// ==========================================
let toastTimer=null;
function showToast(msg,kind="info") {
  const t=document.getElementById("toast"); t.textContent=msg; t.className="toast "+kind;
  if(toastTimer)clearTimeout(toastTimer); toastTimer=setTimeout(()=>t.classList.add("hidden"),2000);
}

// ==========================================
// Auto-save settings on change
// ==========================================
["aspectSelect","resolutionSelect","countSelect","namingSwitch","namingPrefix","namingDelimiter","namingIndexPrefix","namingPadding"].forEach(id=>{
  const el=document.getElementById(id); if(el) el.addEventListener("change",()=>saveSettings());
});
