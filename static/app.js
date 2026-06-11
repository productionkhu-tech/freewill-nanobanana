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
let refCount = 0;                 // number of FILLED ref slots
let refSlotCount = 0;             // total slot count (highest [Image N])
let refFilledSlots = new Set();   // 1-based slot numbers that hold an image
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
  initAlwaysOnTopButton();
  wireProjectNameInputs();
  try {
    const d = await api("/api/delete-confirm-state");
    _skipDeleteConfirm = !!d.skip;
  } catch (e) { /* ignore */ }
});

// Enter = primary action, Escape = cancel. The save / close-save modals
// focus their name input on open; without keyboard bindings the user had
// to mouse over to the Save button every time.
function wireProjectNameInputs() {
  const save = document.getElementById("saveProjectName");
  if (save) {
    save.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.isComposing) {
        e.preventDefault();
        confirmSaveProject();
      } else if (e.key === "Escape") {
        e.preventDefault();
        closeSaveModal();
      }
    });
  }
  const close = document.getElementById("closeProjectName");
  if (close) {
    close.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.isComposing) {
        e.preventDefault();
        closeDialogSave();
      } else if (e.key === "Escape") {
        e.preventDefault();
        closeDialogCancel();
      }
    });
  }
}

// Pin-to-top button. Queries server state on load so reloads keep the
// button's visual in sync with what SetWindowPos actually did to the HWND.
async function initAlwaysOnTopButton() {
  try {
    const d = await api("/api/always-on-top");
    const btn = document.getElementById("alwaysOnTopBtn");
    if (btn) btn.classList.toggle("active", !!d.enabled);
  } catch (_) { /* ignore */ }
}

async function toggleAlwaysOnTop() {
  const btn = document.getElementById("alwaysOnTopBtn");
  if (!btn) return;
  const nextEnabled = !btn.classList.contains("active");
  const d = await api("/api/always-on-top", { method: "POST", body: { enabled: nextEnabled } });
  if (d.ok) {
    btn.classList.toggle("active", !!d.enabled);
    showToast(d.enabled ? "Pinned on top" : "No longer on top", "success");
  } else {
    showToast(d.error || "Toggle failed", "error");
  }
}

// ==========================================
// Release notes "what's new" popup (first launch after update)
// ==========================================
async function checkReleaseNotes() {
  try {
    const d = await api("/api/release-notes-check");
    if (!d.show) return;
    document.getElementById("rnVersion").textContent = d.version || "";
    const prevEl = document.getElementById("rnPrevious");
    prevEl.textContent = "";  // previous version 표시는 제거 (일반 사용자에겐 불필요)
    document.getElementById("rnNotes").innerHTML = _renderReleaseNotes(d.notes || "");
    document.getElementById("releaseNotesModal").classList.remove("hidden");
  } catch (e) { /* network/server down — skip silently */ }
}

// Render a friendly release-notes block from the raw body string.
// Strips markdown noise (**, ##, backticks) and turns bullet lines into a clean list.
function _renderReleaseNotes(raw) {
  if (!raw) return "<div class='rn-empty'>새 버전이 적용되었어요.</div>";
  const esc = (s) => s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  const lines = raw.split(/\r?\n/);
  const out = [];
  let inList = false;
  const flushList = () => { if (inList) { out.push("</ul>"); inList = false; } };

  for (let raw_line of lines) {
    // Strip markdown syntax the user called out — **, ##, backticks
    let line = raw_line
      .replace(/`([^`]+)`/g, "$1")   // inline code
      .replace(/\*\*([^*]+)\*\*/g, "$1")  // **bold**
      .replace(/^#{1,6}\s*/, "")      // ## heading → plain
      .trim();
    if (!line) { flushList(); continue; }
    // Hide infrastructure lines meant for the updater, not the user.
    // Covers: "sha256: <hex>", "sha-256:", bare 64-char hex line, size etc.
    if (/^sha-?256\s*[:=]/i.test(line)) continue;
    if (/^[0-9a-fA-F]{64}$/.test(line)) continue;
    if (/^(size|build|commit|hash)\s*[:=]/i.test(line)) continue;
    if (/^[-*]\s+/.test(line)) {
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${esc(line.replace(/^[-*]\s+/, ""))}</li>`);
    } else {
      flushList();
      out.push(`<p>${esc(line)}</p>`);
    }
  }
  flushList();
  return out.join("");
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
const _NB_CSRF = document.querySelector('meta[name="nb-csrf"]')?.content || "";
async function api(url, opts = {}) {
  opts.headers = { "X-NB-Token": _NB_CSRF, ...(opts.headers || {}) };
  if (opts.body && typeof opts.body === "object" && !(opts.body instanceof FormData)) {
    opts.headers = { "Content-Type": "application/json", ...opts.headers };
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

// Manual update check — user clicks the version label in the footer.
async function manualCheckUpdate() {
  showToast("업데이트 확인 중...", "info");
  const r = await api("/api/check-update", { method: "POST" });
  if (!r.ok) {
    showToast(r.error || "Update check failed", "error");
    return;
  }
  if (r.status === "available") {
    showUpdateConfirmModal(r.current, r.remote);
  } else {
    const kind = r.status === "error" ? "warn" : "info";
    showToast(r.message, kind);
  }
}

// In-page "Update available — install?" dialog. Replaces the Win32
// MessageBox that was unreliable in frozen --windowed EXEs. Lives in
// the webview DOM so it's guaranteed visible and reacts to clicks.
let _updateConfirmShown = false;
function showUpdateConfirmModal(current, remote) {
  if (_updateConfirmShown) return;
  if (document.getElementById("nbUpdateConfirm")) return;
  _updateConfirmShown = true;
  const w = document.createElement("div");
  w.id = "nbUpdateConfirm";
  w.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.55);z-index:99998;display:flex;align-items:center;justify-content:center;font-family:Malgun Gothic,Segoe UI,sans-serif;";
  w.innerHTML = `
    <div style="background:#2C2C2E;border-radius:14px;padding:24px 28px;min-width:360px;max-width:460px;box-shadow:0 10px 40px rgba(0,0,0,.5);color:#F5F5F7">
      <div style="font-size:17px;font-weight:600;margin-bottom:10px">새 버전이 나왔어요</div>
      <div style="font-size:12px;color:#A1A1A6;line-height:1.7;margin-bottom:18px">
        현재 버전: <strong style="color:#F5F5F7">${current}</strong><br>
        최신 버전: <strong style="color:#D4A574">${remote}</strong><br>
        <span style="color:#636366">지금 설치하면 앱이 자동으로 다시 열립니다.</span>
      </div>
      <div style="display:flex;justify-content:flex-end;gap:8px">
        <button id="nbUpdateNow" style="background:#D4A574;color:#1C1C1E;border:none;border-radius:8px;height:34px;padding:0 18px;font-size:12px;font-weight:700;cursor:pointer">지금 설치</button>
      </div>
    </div>`;
  document.body.appendChild(w);
  document.getElementById("nbUpdateNow").addEventListener("click", async () => {
    w.remove();
    _updateConfirmShown = false;
    showUpdateOverlay();
    const r = await api("/api/apply-update", { method: "POST" });
    if (!r.ok) {
      hideUpdateOverlay();
      showToast(r.error || "업데이트 시작 실패", "error");
    }
  });
}

function showUpdateOverlay() {
  if (document.getElementById("nbUpdateOverlay")) return;
  const o = document.createElement("div");
  o.id = "nbUpdateOverlay";
  o.style.cssText = "position:fixed;inset:0;background:rgba(12,12,14,0.96);z-index:99999;display:flex;align-items:center;justify-content:center;color:#F5F5F7;font-family:Malgun Gothic,Segoe UI,sans-serif";
  o.innerHTML = `
    <div style="text-align:center;padding:40px;min-width:340px">
      <div id="nbUpdateLabel" style="font-size:22px;font-weight:600;margin-bottom:10px">업데이트 준비 중…</div>
      <div id="nbUpdateSub" style="font-size:13px;color:#A1A1A6;line-height:1.7;margin-bottom:22px">서버에서 새 버전을 받아오고 있습니다.</div>
      <div style="width:280px;height:6px;background:#2C2C2E;border-radius:999px;overflow:hidden;margin:0 auto">
        <div id="nbUpdateBar" style="height:100%;width:0%;background:#D4A574;transition:width .2s linear;border-radius:999px"></div>
      </div>
      <div style="margin-top:18px;font-size:11px;color:#636366">잠시 후 앱이 자동으로 다시 열립니다.</div>
    </div>`;
  document.body.appendChild(o);
}
function hideUpdateOverlay() {
  const o = document.getElementById("nbUpdateOverlay");
  if (o) o.remove();
}

// ==========================================
// Settings
// ==========================================
async function loadSettings() {
  const d = await api("/api/settings");
  if (!d.model) return;
  document.getElementById("modelSelect").value = d.model;
  // 저장된 값을 적용하기 전에 모델 스펙으로 드롭다운을 먼저 재구성 — 그래야
  // 저장된 옵션(GPT-2 quality 등)이 <select>에 실제로 존재함.
  applyModelSpec(d.model, {
    aspect: d.aspect,
    resolution: d.resolution,
    count: String(d.count),
    quality: d.quality || "high",
  });
  // H7: applyModelSpec() above already populated AND selected each dropdown
  // (loaded value, else this model's default). Raw-setting .value here would
  // bypass that fallback and blank out any value the model no longer offers
  // (e.g. an old 2.5-flash project saved at 4K). So we intentionally don't.
  // Restore Custom pixel inputs (raw user values) then re-toggle the wrap.
  const cwEl = document.getElementById("customW"), chEl = document.getElementById("customH");
  if (cwEl) cwEl.value = d.custom_w || 1024;
  if (chEl) chEl.value = d.custom_h || 1024;
  if (typeof toggleCustomWrap === "function") toggleCustomWrap();
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
  const qs = document.getElementById("qualitySelect");
  const r = await api("/api/settings", { method: "POST", body: {
    model: document.getElementById("modelSelect").value,
    aspect: document.getElementById("aspectSelect").value,
    resolution: document.getElementById("resolutionSelect").value,
    quality: qs ? qs.value : "high",
    custom_w: parseInt(document.getElementById("customW")?.value) || 1024,
    custom_h: parseInt(document.getElementById("customH")?.value) || 1024,
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
  if (r && r.ok === false) {
    // Surface the failure — silent loss of settings is how users end up
    // fighting the UI for 10 minutes before noticing.
    showToast(r.error || "Settings save failed", "error");
  }
}

// ==========================================
// Per-model dropdown spec (GPT Image 2 support)
// ==========================================
// aspects[0] === "auto" is the UI sentinel (shown as "Auto"); its select value
// is the lowercase "auto" the backend keys on. defaultAspect/Resolution are the
// value SELECTED by default and the fallback when a loaded value isn't offered.
const MODEL_SPECS = {
  "gemini-2.5-flash-image": {
    aspects: ["auto","1:1","2:3","3:2","3:4","4:3","4:5","5:4","9:16","16:9","21:9"],
    resolutions: ["1K"],
    counts: ["1","2","3","4","5","6","7","8","9","10"],
    showQuality: false,
    defaultAspect: "16:9", defaultResolution: "1K",
    hint: "10 RPM limit — auto-throttled to ~8 RPM",
    refHint: "Flash model supports up to 3 reference images.",
  },
  "gemini-3-pro-image": {
    aspects: ["auto","1:1","2:3","3:2","3:4","4:3","4:5","5:4","9:16","16:9","21:9"],
    resolutions: ["1K","2K","4K"],
    counts: ["1","2","3","4","5","6","7","8","9","10"],
    showQuality: false,
    defaultAspect: "16:9", defaultResolution: "2K",
    hint: "10 RPM limit — auto-throttled to ~8 RPM",
    refHint: "3rd-gen models support up to 14 reference images.",
  },
  "gemini-3.1-flash-image": {
    aspects: ["auto","1:1","2:3","3:2","3:4","4:3","4:5","5:4","9:16","16:9","21:9","1:4","4:1","1:8","8:1"],
    resolutions: ["512px","1K","2K","4K"],
    counts: ["1","2","3","4","5","6","7","8","9","10"],
    showQuality: false,
    defaultAspect: "16:9", defaultResolution: "2K",
    hint: "10 RPM limit — auto-throttled to ~8 RPM",
    refHint: "3rd-gen models support up to 14 reference images.",
  },
  "gpt-image-2": {
    aspects: ["auto","1:1","3:2","2:3","4:3","3:4","4:5","5:4","16:9","9:16","21:9","9:21","3:1","1:3","custom"],
    resolutions: ["1K","2K","4K"],
    counts: ["1","2","3","4","5","6","7","8","9","10"],
    showQuality: true,
    defaultAspect: "1:1", defaultResolution: "1K", defaultQuality: "high",
    hint: "OpenAI Image API — generation may take up to ~2 min.",
    refHint: "Auto matches the 1st reference's ratio. [Image N] tags don't work — describe refs in the prompt.",
  },
};

// The Gemini API token is "512px" (NOT "0.5K" — verified: 0.5K returns 400).
// v2026-06-12 01/02 shipped the wrong "0.5K" token; migrate it back here.
const _RES_MIGRATIONS = { "0.5K": "512px" };
function migrateResolution(r) { return _RES_MIGRATIONS[r] || r; }

// 옛 -preview 이름이 들어오면 자동 치환.
const _MODEL_ID_MIGRATIONS = {
  "gemini-3-pro-image-preview":     "gemini-3-pro-image",
  "gemini-3.1-flash-image-preview": "gemini-3.1-flash-image",
};
function migrateModelId(m) {
  return _MODEL_ID_MIGRATIONS[m] || m;
}

function getModelSpec(model) {
  model = migrateModelId(model);
  return MODEL_SPECS[model] || MODEL_SPECS["gemini-3-pro-image"];
}

function repopulateSelect(id, values, preferred, fallback) {
  const sel = document.getElementById(id);
  if (!sel) return;
  const prev = preferred || sel.value;
  sel.innerHTML = "";
  values.forEach(v => {
    const opt = document.createElement("option");
    opt.value = v;
    opt.textContent = (v === "auto") ? "Auto" : (v === "custom") ? "Custom" : v;  // sentinels capitalised
    sel.appendChild(opt);
  });
  // H7: prefer the loaded value, else the model's sane default, else last.
  if (values.includes(prev)) sel.value = prev;
  else if (fallback && values.includes(fallback)) sel.value = fallback;
  else sel.value = values[values.length - 1];
}

function applyModelSpec(model, preserved) {
  const spec = getModelSpec(model);
  // H8: migrate the wrong "0.5K" token (shipped v1201/02) back to "512px".
  const prefRes = preserved ? migrateResolution(preserved.resolution) : undefined;
  repopulateSelect("aspectSelect",     spec.aspects,     preserved?.aspect, spec.defaultAspect);
  repopulateSelect("resolutionSelect", spec.resolutions, prefRes,           spec.defaultResolution);
  repopulateSelect("countSelect",      spec.counts,      preserved?.count);
  if (spec.showQuality) {
    repopulateSelect("qualitySelect", ["low","medium","high","auto"], preserved?.quality, spec.defaultQuality || "high");
    const qw = document.getElementById("qualityWrap");
    if (qw) qw.style.display = "";
  } else {
    const qw = document.getElementById("qualityWrap");
    if (qw) qw.style.display = "none";
  }
  const hintEl = document.getElementById("modelHint");
  if (hintEl) hintEl.textContent = spec.hint;
  const isGpt2 = (model === "gpt-image-2");
  const refHintEl = document.getElementById("refLimitHint");
  if (refHintEl) {
    refHintEl.textContent = spec.refHint;
    refHintEl.classList.toggle("hint-warn", isGpt2);
  }
  // Show/hide the Custom pixel inputs for the current model+aspect.
  if (typeof toggleCustomWrap === "function") toggleCustomWrap();
}

function onModelChange() {
  const model = document.getElementById("modelSelect").value;
  applyModelSpec(model);
  saveSettings();
}

// ==========================================
// GPT-2 Custom pixel input
// ==========================================
let _customLockOn = true;
let _customDebounce = null;
let _customRatio = null;   // proportion held while ratio-lock is on
let _lastRefDims;          // last seen slot-1 "WxH" (undefined=startup, null=none)
let _customUserEdited = false;  // user typed/preset a deliberate size -> stop auto-following refs
let _customFrac = null;    // exact ratio "p:q" from the last preset click (null = free input)

function _cCeil16(n){ return Math.max(16, Math.ceil(n/16)*16); }
function _cFloor16(n){ return Math.max(16, Math.floor(n/16)*16); }
function _cRound16(n){ return Math.max(16, Math.round(n/16)*16); }
function _cGcd(a,b){ a=Math.abs(Math.round(a)); b=Math.abs(Math.round(b)); while(b){ const t=a%b; a=b; b=t; } return a||1; }
function _cLcm(a,b){ return a/_cGcd(a,b)*b; }
// Largest WxH at EXACTLY ratio p:q (both 16-multiples) within every gpt-image-2
// cap (edge<=3840, pixels<=8.29M, ratio<=3:1). Returns [W,H] or null.
function _customMaxAtRatio(p, q) {
  const g=_cGcd(p,q); p=p/g; q=q/g;
  if (p > 3*q) { p=3; q=1; } else if (q > 3*p) { p=1; q=3; }   // clamp to <=3:1
  const t0=_cLcm(16/_cGcd(16,p), 16/_cGcd(16,q));
  let best=null;
  for (let m=1; m<100000; m++) {
    const t=t0*m, W=p*t, H=q*t;
    if (Math.max(W,H) > 3840 || W*H > 8294400) break;
    best=[W,H];
  }
  return best;
}
// Clean ratio label: "16:9"/"4:3" for named ratios, else "2.353:1" decimal.
function _ratioLabel(w, h) {
  const g = _cGcd(w, h), p = w / g, q = h / g;
  if (p <= 21 && q <= 21) return `${p}:${q}`;
  return `${(Math.max(w, h) / Math.min(w, h)).toFixed(3)}:1`;
}

// Live-preview mirror of app.py `_gpt2_custom_size`. The server re-corrects on
// send (double-correction), so this is advisory only — but matches the backend.
function gpt2CustomSize(w, h) {
  w = Math.max(16, Math.round(Number(w) || 0));
  h = Math.max(16, Math.round(Number(h) || 0));
  const notes = [];
  if (w > 3*h) { h = _cCeil16(w/3); notes.push("3:1 클램프"); }
  else if (h > 3*w) { w = _cCeil16(h/3); notes.push("3:1 클램프"); }
  if (Math.max(w,h) > 3840) {
    const s = 3840/Math.max(w,h);
    w = Math.max(16, Math.floor(w*s)); h = Math.max(16, Math.floor(h*s));
    notes.push("변 3840 제한");
  }
  const aw = _cRound16(w), ah = _cRound16(h);
  if ((aw !== w || ah !== h) && !notes.length) notes.push("16배수 정렬");
  w = aw; h = ah;
  if (w*h > 8294400) {            // scale BOTH down proportionally (keep ratio)
    const s = Math.sqrt(8294400 / (w*h));
    w = _cFloor16(w*s); h = _cFloor16(h*s);
    notes.push("최대픽셀 축소");
  }
  while (w*h < 655360) { if (w<=h) w=_cCeil16(w+1); else h=_cCeil16(h+1);
    if (!notes.includes("최소픽셀 확대")) notes.push("최소픽셀 확대"); }
  if (w > 3*h) h = _cCeil16(w/3);
  else if (h > 3*w) w = _cCeil16(h/3);
  return { w, h, notes };
}

function _fmtPx(n){ return (n/1e6).toFixed(2) + "M px"; }

function updateCustomPreview() {
  const wEl = document.getElementById("customW"), hEl = document.getElementById("customH");
  const send = document.getElementById("customSend"), status = document.getElementById("customStatus");
  if (!wEl || !hEl || !send) return;
  const rw = wEl.value.trim(), rh = hEl.value.trim();
  if (!rw || !rh || Number(rw) <= 0 || Number(rh) <= 0) {
    send.textContent = "W·H를 입력하세요"; send.className = "custom-send empty";
    if (status) status.textContent = "";
    return;
  }
  const { w, h, notes } = gpt2CustomSize(rw, rh);
  send.textContent = `→ 전송: ${w} × ${h}`;
  send.className = "custom-send" + (notes.length ? " adjusted" : " ok");
  if (status) {
    status.textContent = `${_ratioLabel(w, h)} · ${_fmtPx(w*h)} · ` + (notes.length ? "⚠ " + notes.join(", ") : "✓ valid");
    status.className = "custom-status" + (notes.length ? " adjusted" : " ok");
  }
}

function customSizeActive() {
  const model = document.getElementById("modelSelect").value;
  const asp = document.getElementById("aspectSelect").value;
  return model === "gpt-image-2" && asp === "custom";
}

function toggleCustomWrap() {
  const wrap = document.getElementById("customSizeWrap");
  const active = customSizeActive();
  if (wrap) wrap.style.display = active ? "" : "none";
  // Resolution is meaningless in custom mode (pixels ARE the resolution).
  const resSel = document.getElementById("resolutionSelect");
  if (resSel) {
    resSel.disabled = active;
    const box = resSel.closest("div");
    if (box) box.style.opacity = active ? "0.4" : "";
  }
  if (active) updateCustomPreview();
}

function _customDebouncedSave() {
  if (_customDebounce) clearTimeout(_customDebounce);
  _customDebounce = setTimeout(() => { updateCustomPreview(); saveSettings(); }, 200);
}

// Auto-follow reference slot 1: when it's added/changed/removed while Custom is
// active, sync W/H to its REAL dimensions (no manual "ref" chip click needed).
// The first call after startup only RECORDS the dims, so a restored/typed
// custom value isn't clobbered on load — only genuine ref changes re-fill.
function _applyCustomRefFill(refs) {
  const first = (refs || []).find(r => !r.empty && r.w > 0 && r.h > 0);
  const refBtn = document.getElementById("customRefBtn");   // grey out when no ref present
  if (refBtn) refBtn.disabled = !first;
  const key = first ? (first.w + "x" + first.h) : null;
  if (_lastRefDims === undefined) { _lastRefDims = key; return; }  // startup: record only
  if (key === _lastRefDims) return;                                // unchanged: don't clobber
  const hadRef = (_lastRefDims !== null);
  _lastRefDims = key;
  // A deliberate user value (typed or preset) is NEVER clobbered by a ref change.
  // Use the "ref" chip to opt back into following the reference.
  if (_customUserEdited) return;
  if (!customSizeActive()) return;
  const wEl = document.getElementById("customW"), hEl = document.getElementById("customH");
  if (!wEl || !hEl) return;
  if (first) {                       // ref added/changed -> follow its dimensions
    wEl.value = first.w; hEl.value = first.h;
    _customRatio = first.w / first.h;
  } else if (hadRef) {               // last ref just removed -> reset to default 1024x1024
    wEl.value = 1024; hEl.value = 1024;
    _customRatio = 1;
  } else {
    return;
  }
  updateCustomPreview(); saveSettings();
}

function initCustomSize() {
  const wEl = document.getElementById("customW"), hEl = document.getElementById("customH");
  const lock = document.getElementById("customLock");
  if (!wEl || !hEl) return;
  const capRatio = () => { const w = Number(wEl.value), h = Number(hEl.value); if (w>0 && h>0) _customRatio = w/h; };
  wEl.addEventListener("focus", capRatio);
  hEl.addEventListener("focus", capRatio);
  wEl.addEventListener("input", () => {
    _customUserEdited = true; _customFrac = null;   // free typing -> not a preset ratio
    if (_customLockOn && _customRatio) hEl.value = _cRound16(Number(wEl.value) / _customRatio);
    _customDebouncedSave();
  });
  hEl.addEventListener("input", () => {
    _customUserEdited = true; _customFrac = null;
    if (_customLockOn && _customRatio) wEl.value = _cRound16(Number(hEl.value) * _customRatio);
    _customDebouncedSave();
  });
  if (lock) lock.addEventListener("click", () => {
    _customLockOn = !_customLockOn;
    lock.classList.toggle("on", _customLockOn);
    if (_customLockOn) capRatio();
  });
  document.querySelectorAll(".custom-chip[data-ar]").forEach(chip => {
    chip.addEventListener("click", () => {
      _customUserEdited = true;
      _customFrac = chip.dataset.frac || null;
      // Preset click = the MAX valid size at that EXACT ratio (one click, never
      // over-cap, no second "최대 화질" press needed).
      const fr = (_customFrac || "1:1").split(":").map(Number);
      const best = _customMaxAtRatio(fr[0], fr[1]);
      if (best) { wEl.value = best[0]; hEl.value = best[1]; _customRatio = best[0] / best[1]; }
      updateCustomPreview(); saveSettings();
    });
  });
  const refBtn = document.getElementById("customRefBtn");
  if (refBtn) refBtn.addEventListener("click", async () => {
    // Pull the FIRST filled slot's ORIGINAL dimensions from the backend
    // (the thumbnail's naturalWidth would be the resized preview, not the ref).
    try {
      const d = await api("/api/refs");
      const first = (d.refs || []).find(r => !r.empty && r.w > 0 && r.h > 0);
      if (first) {
        _customUserEdited = false;   // "match reference" -> opt back into auto-follow
        _customFrac = null;          // ref dims aren't a preset ratio -> let "최대 화질" derive the ratio from these real dims
        wEl.value = first.w; hEl.value = first.h;
        _customRatio = first.w / first.h;
        _lastRefDims = first.w + "x" + first.h;   // keep auto-follow tracker in sync
        updateCustomPreview(); saveSettings();
        return;
      }
    } catch (e) {}
    showToast("레퍼런스 슬롯 1이 비어있어요", "info");
  });
  // "⬆ 최대 화질": fill W/H with the largest valid size at the CURRENT exact ratio.
  const maxBtn = document.getElementById("customMaxBtn");
  if (maxBtn) maxBtn.addEventListener("click", () => {
    let p, q;
    if (_customFrac) {
      [p, q] = _customFrac.split(":").map(Number);   // exact ratio from a preset
    } else {
      const w = Number(wEl.value), h = Number(hEl.value);
      if (!(w > 0 && h > 0)) { showToast("W·H를 먼저 입력하세요", "info"); return; }
      p = w; q = h;                                   // exact ratio from the typed values
    }
    const best = _customMaxAtRatio(p, q);
    if (!best) { showToast("계산할 수 없는 비율이에요", "error"); return; }
    _customUserEdited = true;
    wEl.value = best[0]; hEl.value = best[1];
    _customRatio = best[0] / best[1];
    updateCustomPreview(); saveSettings();
  });
  // Toggle the wrap when the user switches to/from the Custom aspect.
  const asp = document.getElementById("aspectSelect");
  if (asp) asp.addEventListener("change", toggleCustomWrap);
}

function updateRefLimitHint(model) {
  // 원본 슬롯/멘션 코드가 호출하는 진입점 — spec 기반으로 위임해 일관성 유지.
  const refHintEl = document.getElementById("refLimitHint");
  if (refHintEl) refHintEl.textContent = getModelSpec(model).refHint;
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
  ta.placeholder = "Describe the image... Enter=Generate, Shift+Enter=newline, @ mentions refs";
  ta.value = initialText;
  ta.addEventListener("input", (e) => {
    // Normalize "@imageN" -> "[Image N]" before anything else reads the value.
    if (maybeAutoConvertMentions(e, ta)) closeMentionMenu();
    onPromptInput(e, ta);
    syncPromptHighlight(ta, { immediate: _shouldSyncImmediate(e) });
    scheduleSettingsSave();
  });
  ta.addEventListener("keyup", () => _tryShowMention(ta));
  ta.addEventListener("scroll", () => syncPromptHighlight(ta));
  // Catch a "@image1" left unsealed at the caret (user typed it then clicked
  // away without a trailing space) — full-mode convert on blur.
  ta.addEventListener("blur", () => {
    if (_autoConvertImageMentions(ta, true)) {
      syncPromptHighlight(ta, { immediate: true });
      scheduleSettingsSave();
    }
  });
  ta.addEventListener("keydown", (e) => {
    _onAtKeydown(e, ta);     // detect @ directly from the keystroke
    onPromptKeydown(e, ta);
  });
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

// Highlight overlay update.
//
// Korean IME emits many input events per second during composition, so to
// avoid rebuilding the whole overlay on every keystroke we normally batch
// via rAF. But for paste (especially held Ctrl+V) the coalesce leaves the
// overlay's innerHTML one frame behind the textarea's value — the caret
// sits where the new text should be, but the visible (overlay) text is
// still the previous frame's content, so the cursor visibly drifts ahead
// of the rendered characters. Callers pass {immediate:true} from paste
// handlers to bypass the rAF and sync synchronously.
//
// Scroll position sync is ALWAYS immediate (cheap; avoids a second
// frame of lag where the overlay is scrolled one line behind).
const _pendingHighlights = new WeakSet();
function syncPromptHighlight(textarea, opts) {
  const wrap = textarea.closest(".prompt-wrap");
  if (!wrap) return;
  const hl = wrap.querySelector(".prompt-highlight");
  if (!hl) return;
  // Always sync scroll immediately.
  hl.scrollTop = textarea.scrollTop;
  const immediate = opts && opts.immediate;
  if (immediate) {
    // Cancel any pending rAF — we're about to do the work synchronously.
    _pendingHighlights.delete(textarea);
    hl.innerHTML = _buildHighlightedHTML(textarea.value);
    hl.scrollTop = textarea.scrollTop;
    return;
  }
  if (_pendingHighlights.has(textarea)) return;
  _pendingHighlights.add(textarea);
  requestAnimationFrame(() => {
    _pendingHighlights.delete(textarea);
    hl.innerHTML = _buildHighlightedHTML(textarea.value);
    hl.scrollTop = textarea.scrollTop;
  });
}

// Decide whether this input event should bypass rAF coalescing. Paste
// (including repeated Ctrl+V) inserts large chunks at once, so we want
// the overlay content to update the same frame the textarea does.
// IME composition ("insertCompositionText" / "insertFromComposition") stays
// on the rAF path to keep per-keystroke rebuilds cheap.
function _shouldSyncImmediate(e) {
  if (!e) return false;
  const t = e.inputType || "";
  return t === "insertFromPaste"
      || t === "insertReplacementText"
      || t === "insertFromDrop"
      || t === "insertFromYank"
      || t.startsWith("deleteByCut");
}

function setupFixedPromptMention() {
  const fp = document.getElementById("fixedPrompt");
  if (fp) {
    fp.addEventListener("input", (e) => {
      if (maybeAutoConvertMentions(e, fp)) closeMentionMenu();
      onPromptInput(e, fp);
      syncPromptHighlight(fp, { immediate: _shouldSyncImmediate(e) });
      scheduleSettingsSave();
    });
    fp.addEventListener("keyup", () => _tryShowMention(fp));
    fp.addEventListener("scroll", () => syncPromptHighlight(fp));
    fp.addEventListener("blur", () => {
      if (_autoConvertImageMentions(fp, true)) {
        syncPromptHighlight(fp, { immediate: true });
        scheduleSettingsSave();
      }
    });
    fp.addEventListener("keydown", (e) => {
      _onAtKeydown(e, fp);
      onPromptKeydown(e, fp);
    });
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
  document.getElementById("modelSelect").value = "gemini-3-pro-image";
  applyModelSpec("gemini-3-pro-image");
  // Close the mention menu if it was open over a box we're about to wipe —
  // otherwise the menu keeps referencing a detached textarea and the next
  // keystroke throws because mentionTarget.textarea is no longer in the DOM.
  closeMentionMenu();
  const fp = document.getElementById("fixedPrompt");
  fp.value = "";
  // Explicitly sync the highlight overlay — setting .value doesn't fire
  // an input event, so the colored [Image N] chunks would otherwise
  // linger in the overlay layer even though the textarea is empty.
  if (typeof syncPromptHighlight === "function") syncPromptHighlight(fp);
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
// Trigger the mention menu whenever the textarea's current value ends with `@`
// at the cursor position — regardless of whether the event came from input,
// keyup, or an IME composition end. Fixes two bugs:
//   - Korean IME: `input` events are suppressed during composition; the menu
//     was only appearing after the user pressed space.
//   - Direct `@` press: sometimes the first `@` char landed but input fired
//     before selectionStart updated, so the lookup saw the wrong position.
function _tryShowMention(textarea) {
  const pos = textarea.selectionStart;
  if (pos > 0 && textarea.value[pos - 1] === "@" && refFilledSlots.size > 0) {
    showMentionMenu(textarea, pos);
  } else if (mentionMenu) {
    if (textarea.value.substring(0, pos).lastIndexOf("@") === -1) {
      closeMentionMenu();
    }
  }
}

function onPromptInput(e, textarea) {
  _tryShowMention(textarea);
}

// Global IME state (only used by the gallery search debounce)
let _imeComposing = false;
document.addEventListener("compositionstart", () => { _imeComposing = true; }, true);
document.addEventListener("compositionend", (e) => {
  _imeComposing = false;
  const t = e.target;
  if (t && (t.id === "fixedPrompt" || t.classList?.contains("prompt-section-box"))) {
    setTimeout(() => _tryShowMention(t), 0);
  }
}, true);
// Safety nets so the flag can never get stuck on if the IME silently drops
// compositionend (Alt-Tab away mid-compose, window loses focus, IME switch).
window.addEventListener("blur", () => { _imeComposing = false; });
document.addEventListener("visibilitychange", () => {
  if (document.hidden) _imeComposing = false;
});

// Direct `@` keypress handler — works even when the Korean IME is active.
// On a Korean keyboard `@` is Shift+2; with Hangul mode on, the IME can
// hold the char in composition state and the mention menu wouldn't trigger
// until Space commits it. We intercept the keystroke itself and schedule a
// re-check after the browser has written the char into the textarea.
function _onAtKeydown(e, textarea) {
  // Match both the Shift+2 physical key and the produced character
  const isAt = e.key === "@" || (e.shiftKey && (e.key === "2" || e.code === "Digit2"));
  if (!isAt) return;
  // Don't preventDefault — let the @ land in the textarea naturally.
  // Then poll a few frames until it actually appears (handles IME delay).
  let tries = 0;
  const check = () => {
    tries++;
    const pos = textarea.selectionStart;
    const valueHasAt = pos > 0 && textarea.value[pos - 1] === "@";
    if (valueHasAt) {
      _tryShowMention(textarea);
      return;
    }
    if (tries < 20) setTimeout(check, 25);  // up to 500ms
  };
  setTimeout(check, 0);
}

// Treat `[Image N]` like an atomic chip: Backspace at the right edge (with
// or without a trailing space) deletes the whole tag, Delete at the left
// edge eats it forward, and Left/Right arrow jumps over it. This matches
// how mention pills work in Slack/Discord and avoids the broken "[Image"
// fragments the highlight layer can't colorize.
// Backspace also eats the trailing space that insertMention() adds, so one
// Backspace after insertion removes the whole chip + its space. Forward
// Delete only removes the bracketed tag itself — we don't want to start
// munching leading spaces the user typed on purpose.
const _MENTION_RE_LEFT  = /\[Image (\d+)\] ?$/;
const _MENTION_RE_RIGHT = /^\[Image (\d+)\]/;

function _atomicMentionEdit(e, textarea) {
  if (textarea.selectionStart !== textarea.selectionEnd) return false; // honor real selections
  const pos = textarea.selectionStart;
  const value = textarea.value;

  if (e.key === "Backspace") {
    const m = value.slice(0, pos).match(_MENTION_RE_LEFT);
    if (!m) return false;
    const start = pos - m[0].length;
    textarea.value = value.slice(0, start) + value.slice(pos);
    textarea.selectionStart = textarea.selectionEnd = start;
    syncPromptHighlight(textarea);
    scheduleSettingsSave();
    e.preventDefault();
    return true;
  }
  if (e.key === "Delete") {
    const m = value.slice(pos).match(_MENTION_RE_RIGHT);
    if (!m) return false;
    textarea.value = value.slice(0, pos) + value.slice(pos + m[0].length);
    textarea.selectionStart = textarea.selectionEnd = pos;
    syncPromptHighlight(textarea);
    scheduleSettingsSave();
    e.preventDefault();
    return true;
  }
  if (e.key === "ArrowLeft" && !e.shiftKey && !e.ctrlKey) {
    const m = value.slice(0, pos).match(/\[Image (\d+)\]$/);
    if (!m) return false;
    const np = pos - m[0].length;
    textarea.selectionStart = textarea.selectionEnd = np;
    e.preventDefault();
    return true;
  }
  if (e.key === "ArrowRight" && !e.shiftKey && !e.ctrlKey) {
    const m = value.slice(pos).match(/^\[Image (\d+)\]/);
    if (!m) return false;
    const np = pos + m[0].length;
    textarea.selectionStart = textarea.selectionEnd = np;
    e.preventDefault();
    return true;
  }
  return false;
}

function onPromptKeydown(e, textarea) {
  // Handle mention-as-chip first so backspace eats the whole [Image N]
  // before the default single-char delete fires.
  if (!mentionMenu && _atomicMentionEdit(e, textarea)) return;

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

  // Enter (without Shift/Ctrl) → queue a generation.
  //   - Shift+Enter: default newline behavior
  //   - Ctrl+Enter:  handled by the document-level shortcut (setupKeyboardShortcuts).
  //                  Excluding it here prevents a double-fire (both this listener
  //                  and the document bubble-up would call generate() → 2× count).
  //   - Mention menu open: falls through to the insert-mention handler below.
  if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !mentionMenu && !_imeComposing) {
    e.preventDefault();
    generate();
    return;
  }

  if (!mentionMenu) return;
  if (e.key === "ArrowDown") { e.preventDefault(); navigateMention(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); navigateMention(-1); }
  else if (e.key === "Enter" || e.key === "Tab") {
    e.preventDefault();
    // During Korean IME composition, Enter commits the pending char FIRST.
    // If we insert the mention now, the browser will write the @ over our
    // change as it finishes composition. Defer until compositionend fires.
    if (e.isComposing || _imeComposing) {
      const onceEnd = () => {
        textarea.removeEventListener("compositionend", onceEnd);
        setTimeout(insertMention, 0);
      };
      textarea.addEventListener("compositionend", onceEnd);
    } else {
      insertMention();
    }
  }
  else if (e.key === "Escape") { e.preventDefault(); closeMentionMenu(); }
}

function showMentionMenu(textarea, cursorPos) {
  // Idempotent: if the menu is already open for this exact `@` position,
  // don't tear it down. Previously every keyup (including arrow keys)
  // rebuilt the menu, which reset dataset.selected back to 0 — the user
  // could never navigate to Image 2 because navigateMention's increment
  // was undone by the very next keyup.
  if (mentionMenu && mentionTarget &&
      mentionTarget.textarea === textarea &&
      mentionTarget.cursorPos === cursorPos) {
    return;
  }
  closeMentionMenu();
  if (refFilledSlots.size <= 0) return;
  mentionTarget = { textarea, cursorPos };
  mentionMenu = document.createElement("div");
  mentionMenu.className = "mention-menu";
  mentionMenu.dataset.selected = "0";
  // Offer only FILLED slots — e.g. with slot 2 empty the menu lists
  // "Image 1" and "Image 3". Each button carries its real slot number.
  const slots = [...refFilledSlots].sort((a, b) => a - b);
  slots.forEach((slotNum, i) => {
    const btn = document.createElement("button");
    btn.className = "mention-item" + (i === 0 ? " active" : "");
    btn.textContent = `Image ${slotNum}`;
    btn.dataset.slot = String(slotNum);
    btn.addEventListener("mousedown", (e) => {
      e.preventDefault();
      mentionMenu.dataset.selected = String(i);
      insertMention();
    });
    mentionMenu.appendChild(btn);
  });
  document.body.appendChild(mentionMenu);

  // Reposition now AND after a short delay (in case layout wasn't settled
  // yet — IME timing, fonts loading, etc.). Without this, first rect read
  // sometimes returned 0s and menu rendered off-screen showing a blank box.
  positionMentionMenu(textarea);
  setTimeout(() => positionMentionMenu(textarea), 30);
}

function positionMentionMenu(textarea) {
  if (!mentionMenu) return;
  const rect = textarea.getBoundingClientRect();
  // If rect is still 0×0 (textarea not yet laid out / hidden), defer
  if (rect.width === 0 && rect.height === 0) return;

  const menuH = mentionMenu.offsetHeight || (refFilledSlots.size * 30 + 10);
  const menuW = mentionMenu.offsetWidth || 160;
  const vh = window.innerHeight;
  const vw = window.innerWidth;

  // Always anchor below the textarea. If no room, flip above.
  const below = rect.bottom + 4;
  const above = rect.top - menuH - 4;
  let top;
  if (below + menuH <= vh) top = below;
  else if (above >= 4) top = above;
  else top = Math.max(4, vh - menuH - 4);

  let left = Math.max(4, Math.min(rect.left + 20, vw - menuW - 4));

  mentionMenu.style.left = `${left}px`;
  mentionMenu.style.top = `${top}px`;
  mentionMenu.style.bottom = "auto";
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
  // The selected button carries the real slot number in data-slot.
  const items = mentionMenu.querySelectorAll(".mention-item");
  const sel = parseInt(mentionMenu.dataset.selected || "0", 10);
  const slotNum = parseInt((items[sel] && items[sel].dataset.slot) || "1", 10);
  const tag = `[Image ${slotNum}]`;
  const ta = mentionTarget.textarea;
  // Re-read position at insert time; the stored cursorPos can be stale if
  // the IME committed more characters between "@" press and this call.
  const caret = ta.selectionStart;
  const value = ta.value;
  // Find the most recent "@" at or before the caret — that's the one we
  // opened the menu for. Fallback to the stored cursorPos if nothing found.
  let atPos = caret - 1;
  while (atPos >= 0 && value[atPos] !== "@") atPos--;
  if (atPos < 0) atPos = Math.max(0, (mentionTarget.cursorPos || 1) - 1);
  ta.value = value.substring(0, atPos) + tag + " " + value.substring(caret);
  const newCaret = atPos + tag.length + 1;
  ta.selectionStart = ta.selectionEnd = newCaret;
  ta.focus();
  if (typeof syncPromptHighlight === "function") syncPromptHighlight(ta);
  closeMentionMenu();
  scheduleSettingsSave();
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
// Ref-slot mentions  ([Image N]  <->  @imageN)
// ==========================================
// MODEL: the prompt is the anchor. A [Image N] / @imageN mention's NUMBER is
// whatever the user wrote — the app never renumbers it. Only the FORM toggles
// with the ref-slot state:
//   - [Image N]  (blue chip)  = slot N currently holds an image
//   - @imageN    (grey text)  = slot N is empty / doesn't exist
// Delete a ref -> its slot empties -> [Image N] becomes @imageN. Add a ref
// into that slot -> @imageN becomes [Image N] again. Reorder swaps slot
// contents but the prompt text is left untouched.
//
// `@` must not be preceded by an alphanumeric (so "email@image1" is left
// alone). In typed mode the digits must be sealed by a following non-digit
// (so typing "@image1" then "2" becomes "@image12", not "[Image 1]2").
// In paste/blur/pre-generate mode end-of-string also counts as sealed.
const _MENTION_TOKEN_SEALED = /(?<![A-Za-z0-9])@[ \t]*image[ \t]*(\d+)(?=\D)/gi;
const _MENTION_TOKEN_FULL   = /(?<![A-Za-z0-9])@[ \t]*image[ \t]*(\d+)/gi;

// Convert typed/pasted "@imageN" -> "[Image N]" for slots that currently hold
// an image. Pure textarea text; the queue snapshots prompt text at
// /api/generate time so this never interacts with queue ordering.
function _autoConvertImageMentions(ta, pasteMode) {
  if (!ta || refFilledSlots.size === 0) return false;
  const value = ta.value;
  const caret = ta.selectionStart;
  const re = pasteMode ? _MENTION_TOKEN_FULL : _MENTION_TOKEN_SEALED;
  re.lastIndex = 0;
  let out = "";
  let lastIdx = 0;
  let delta = 0;          // length change applied to text strictly before caret
  let caretInMatch = -1;  // if caret lands inside a converted match, snap here
  let changed = false;
  let m;
  while ((m = re.exec(value)) !== null) {
    const n = parseInt(m[1], 10);
    if (!refFilledSlots.has(n)) continue;   // slot N not filled — leave verbatim
    const replacement = `[Image ${n}]`;
    const s = m.index;
    const end = m.index + m[0].length;
    out += value.slice(lastIdx, s) + replacement;
    lastIdx = end;
    changed = true;
    if (caret >= end) {
      delta += replacement.length - m[0].length;
    } else if (caret > s) {
      caretInMatch = out.length;
    }
  }
  if (!changed) return false;
  out += value.slice(lastIdx);
  ta.value = out;
  const newCaret = caretInMatch >= 0 ? caretInMatch : caret + delta;
  ta.selectionStart = ta.selectionEnd = Math.max(0, Math.min(newCaret, out.length));
  return true;
}

// Called from a prompt box's `input` listener. Paste/drop convert eagerly
// (the inserted chunk is complete); plain typing only converts sealed tokens.
function maybeAutoConvertMentions(e, ta) {
  if (e && e.isComposing) return false;          // mid-IME — defer
  const paste = !!e && (e.inputType === "insertFromPaste" ||
                        e.inputType === "insertFromDrop");
  return _autoConvertImageMentions(ta, paste);
}

// Two-way re-sync of every prompt mention to the current ref-slot state.
// The mention number is fixed; only the form changes:
//   [Image N] -> @imageN   when slot N is empty / out of range
//   @imageN   -> [Image N] when slot N is filled
// Called from refreshRefs() after any add/delete/reorder, and from generate()
// so the server snapshots canonical [Image N] for every filled slot.
// Returns true if any box changed.
function syncMentionsToRefSlots() {
  const boxes = [document.getElementById("fixedPrompt"),
                 ...document.querySelectorAll(".prompt-section-box")];
  let any = false;
  boxes.forEach(ta => {
    if (!ta) return;
    let v = ta.value;
    // de-activate: [Image N] -> @imageN when slot N no longer holds an image
    v = v.replace(/\[Image (\d+)\]/g, (m, n) =>
      refFilledSlots.has(parseInt(n, 10)) ? m : `@image${n}`);
    // activate: @imageN -> [Image N] when slot N is filled
    v = v.replace(_MENTION_TOKEN_FULL, (m, n) =>
      refFilledSlots.has(parseInt(n, 10)) ? `[Image ${parseInt(n, 10)}]` : m);
    if (v !== ta.value) {
      ta.value = v;
      any = true;
      if (typeof syncPromptHighlight === "function") {
        syncPromptHighlight(ta, { immediate: true });
      }
    }
  });
  return any;
}

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
// Render an empty ref slot — a placeholder that holds slot number i so the
// [Image i+1] mention keeps its meaning. The whole cell is a drop target
// (fill it) and a reorder drop target; clicking opens the file picker.
function _renderEmptyRefSlot(cell, i) {
  // Structurally mirror a filled cell — media box + label + button — so an
  // empty slot is exactly the same height and the grid columns line up.
  cell.className = "ref-cell ref-cell-empty";
  cell.title = `Image ${i + 1} — empty slot. Drop or click to fill.`;
  const IDLE_HINT = "비어 있음";
  const DROP_HINT = "여기에 놓기";

  const media = document.createElement("div");
  media.className = "ref-media ref-empty-media";
  const hint = document.createElement("div");
  hint.className = "ref-empty-hint";
  hint.textContent = IDLE_HINT;
  media.appendChild(hint);
  cell.appendChild(media);

  const lbl = document.createElement("div");
  lbl.className = "ref-label";
  lbl.textContent = `[Image ${i + 1}]`;
  cell.appendChild(lbl);

  // Sits in the same row as a filled cell's Change button -> equal height.
  const fillBtn = document.createElement("button");
  fillBtn.className = "ref-change";
  fillBtn.textContent = "채우기";
  fillBtn.addEventListener("click", (e) => { e.stopPropagation(); replaceRef(i); });
  cell.appendChild(fillBtn);

  cell.addEventListener("click", () => replaceRef(i));
  const clearHover = () => {
    cell.classList.remove("reorder-over");
    cell.classList.remove("drop-target");
    hint.textContent = IDLE_HINT;
  };
  cell.addEventListener("dragover", (e) => {
    if (!e.dataTransfer) return;
    e.preventDefault();
    if (e.dataTransfer.types.includes("application/x-nb-ref-reorder")) {
      e.dataTransfer.dropEffect = "move";
      cell.classList.add("reorder-over");
    } else {
      cell.classList.add("drop-target");
    }
    // Make it unmistakable that this drop goes INTO this slot (vs adding a
    // brand-new image when dropped on empty ref-area space).
    hint.textContent = DROP_HINT;
  });
  cell.addEventListener("dragleave", (e) => {
    if (!cell.contains(e.relatedTarget)) clearHover();
  });
  cell.addEventListener("drop", async (e) => {
    e.preventDefault();
    e.stopPropagation();
    clearHover();
    const reorderRaw = e.dataTransfer ? e.dataTransfer.getData("application/x-nb-ref-reorder") : "";
    if (reorderRaw !== "" && reorderRaw != null) {
      const from = parseInt(reorderRaw, 10);
      if (!Number.isNaN(from) && from !== i) reorderRefs(from, i);
      return;
    }
    await _fillSlotFromDrop(e, i);
  });
}

// Fill slot i from a drop event (gallery-card internal drag or external file).
async function _fillSlotFromDrop(e, i) {
  const internalPath = e.dataTransfer?.getData("application/x-nb-gallery-path");
  if (internalPath) {
    const r = await api(`/api/refs/replace-from-path/${i}`, {
      method: "POST", body: { filepath: internalPath },
    });
    if (r.ok) {
      if (!r.unchanged) await refreshRefs();
      showToast(r.unchanged ? "Same image — no change" : "Reference set", "success");
    } else {
      showToast(r.error || "Failed", "error");
    }
    return true;
  }
  const files = e.dataTransfer?.files;
  if (files && files.length) {
    const f = files[0];
    const ext = (f.name || "").split(".").pop().toLowerCase();
    if (!["png", "jpg", "jpeg", "webp", "bmp"].includes(ext)) {
      showToast("Unsupported format", "warn");
      return true;
    }
    const form = new FormData();
    form.append("file", f);
    const r = await api(`/api/refs/replace/${i}`, { method: "POST", body: form });
    if (r.ok) { await refreshRefs(); showToast("Reference set", "success"); }
    else { showToast(r.error || "Failed", "error"); }
    return true;
  }
  return false;
}

async function refreshRefs() {
  const d = await api("/api/refs");
  refCount = d.count || 0;
  refSlotCount = d.slot_count || (d.refs ? d.refs.length : 0);
  refFilledSlots = new Set();
  (d.refs || []).forEach((ref, i) => { if (!ref.empty) refFilledSlots.add(i + 1); });
  // Auto-follow slot-1 dimensions into the Custom inputs (no chip click needed).
  if (typeof _applyCustomRefFill === "function") _applyCustomRefFill(d.refs);
  // A ref slot just changed (add/delete/reorder). Re-sync every prompt
  // mention to slot state: [Image N] de-activates to @imageN text when slot N
  // is empty, @imageN re-activates to [Image N] when slot N is filled. The
  // mention NUMBER never changes — the prompt is the anchor.
  if (syncMentionsToRefSlots()) {
    closeMentionMenu();
    scheduleSettingsSave();
  }
  const grid = document.getElementById("refGrid");
  const empty = document.getElementById("refEmpty");
  grid.innerHTML = "";
  if (!d.refs || d.refs.length === 0) { empty.style.display = "block"; return; }
  empty.style.display = "none";
  d.refs.forEach((ref, i) => {
    const cell = document.createElement("div");
    if (ref.empty) { _renderEmptyRefSlot(cell, i); grid.appendChild(cell); return; }
    cell.className = "ref-cell" + (ref.pinned ? " pinned" : "");

    // Drag-to-reorder: the whole cell is a drag source. The payload is the
    // cell's index under a private MIME type so drop handlers can tell a
    // reorder apart from an external file drop or a gallery-card drag.
    cell.draggable = true;
    cell.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("application/x-nb-ref-reorder", String(i));
      e.dataTransfer.effectAllowed = "move";
      cell.classList.add("reorder-dragging");
    });
    cell.addEventListener("dragend", () => {
      cell.classList.remove("reorder-dragging");
      cell.classList.remove("reorder-over");
    });
    cell.addEventListener("dragover", (e) => {
      if (e.dataTransfer && e.dataTransfer.types.includes("application/x-nb-ref-reorder")) {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        cell.classList.add("reorder-over");
      }
    });
    cell.addEventListener("dragleave", (e) => {
      // relatedTarget is where the pointer went; only clear when it left the cell.
      if (!cell.contains(e.relatedTarget)) cell.classList.remove("reorder-over");
    });

    // Drop policy (v2008):
    //   - Reorder drag (another ref cell)        -> move this slot
    //   - Drop on the Change button of this slot -> REPLACE this slot
    //   - Drop anywhere else on the cell         -> fall through to refArea (ADD)
    //
    // Earlier builds (v2005-v2007) also supported a "hover-hold" on the
    // cell image to replace, but the UX was unreliable and confusing. The
    // Change button is now the only REPLACE target.
    cell.addEventListener("drop", async (e) => {
      // Reorder drag from another ref cell — handled before everything else.
      const reorderRaw = e.dataTransfer ? e.dataTransfer.getData("application/x-nb-ref-reorder") : "";
      if (reorderRaw !== "" && reorderRaw != null) {
        e.preventDefault();
        e.stopPropagation();
        cell.classList.remove("reorder-over");
        const from = parseInt(reorderRaw, 10);
        if (!Number.isNaN(from) && from !== i) reorderRefs(from, i);
        return;
      }
      const onChangeBtn = e.target.closest(".ref-change");
      if (!onChangeBtn) {
        // No preventDefault / no stopPropagation -> bubbles to refArea's
        // ondrop="onRefDrop(event)" which runs the ADD path.
        return;
      }
      e.preventDefault();
      e.stopPropagation();
      onChangeBtn.classList.remove("drop-target");

      // Internal drag from a gallery card: replace this cell with the
      // gallery image (JSON body, no file upload needed). Server-side
      // duplicate guard keeps ref_path_list unique.
      const internalPath = e.dataTransfer?.getData("application/x-nb-gallery-path");
      if (internalPath) {
        const r = await api(`/api/refs/replace-from-path/${i}`, {
          method: "POST",
          body: { filepath: internalPath },
        });
        if (r.ok) {
          if (!r.unchanged) await refreshRefs();
          showToast(r.unchanged ? "Same image — no change" : "Reference replaced", "success");
        } else {
          showToast(r.error || "Replace failed", "error");
        }
        return;
      }

      const files = e.dataTransfer?.files;
      if (!files || !files.length) return;
      const f = files[0];
      const ext = (f.name || "").split(".").pop().toLowerCase();
      if (!["png", "jpg", "jpeg", "webp", "bmp"].includes(ext)) {
        showToast("Unsupported format", "warn");
        return;
      }
      // Replace in place — server-side endpoint preserves position + pin state.
      const form = new FormData();
      form.append("file", f);
      const r = await api(`/api/refs/replace/${i}`, { method: "POST", body: form });
      if (r.ok) {
        await refreshRefs();
        showToast("Reference replaced", "success");
      } else {
        showToast(r.error || "Replace failed", "error");
      }
    });

    // Media frame with image + overlay buttons
    const media = document.createElement("div");
    media.className = "ref-media";

    const img = document.createElement("img");
    img.src = `/api/refs/thumb/${i}?t=${Date.now()}`;
    img.alt = `ref ${i + 1}`;
    // Kill the native image drag so dragging the thumbnail moves the whole
    // cell (reorder) instead of starting an image drag with no payload.
    img.draggable = false;
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

    // Change button — doubles as an explicit drop-to-replace target so the
    // user can disambiguate ADD vs REPLACE without needing empty real estate.
    const chgBtn = document.createElement("button");
    chgBtn.className = "ref-change";
    chgBtn.textContent = "Change";
    chgBtn.addEventListener("click", () => replaceRef(i));
    // Counter pattern — child-less button so this is mostly defensive, but
    // matches the refArea dragDepth style.
    let _chgDragDepth = 0;
    chgBtn.addEventListener("dragenter", (e) => {
      // A reorder drag isn't a replace — don't show the replace highlight.
      if (e.dataTransfer && e.dataTransfer.types.includes("application/x-nb-ref-reorder")) return;
      e.preventDefault();
      _chgDragDepth++;
      chgBtn.classList.add("drop-target");
    });
    chgBtn.addEventListener("dragover", (e) => {
      if (e.dataTransfer && e.dataTransfer.types.includes("application/x-nb-ref-reorder")) return;
      e.preventDefault();
    });
    chgBtn.addEventListener("dragleave", () => {
      _chgDragDepth = Math.max(0, _chgDragDepth - 1);
      if (_chgDragDepth === 0) chgBtn.classList.remove("drop-target");
    });
    cell.appendChild(chgBtn);

    grid.appendChild(cell);
  });
}

// v2026-05-1101: Native browser file picker via the hidden <input type="file"
// multiple> in index.html. Replaces the tkinter askopenfilenames route, which
// was unreliable in PyInstaller+WebView2 (often returned only the first of
// several Ctrl/Shift-selected files). Same /api/refs/upload endpoint as the
// drag-and-drop path, so all the dedupe + slot-limit + PIL validation runs
// once in `upload_refs` regardless of how the file got picked.
async function onRefFilesChosen(inputEl) {
  const files = inputEl.files;
  showToast(`Picked ${files ? files.length : 0} file(s)...`, "info");
  if (!files || !files.length) return;
  const form = new FormData();
  let appended = 0;
  for (const f of files) { form.append("files", f); appended++; }
  if (appended === 0) {
    showToast("No valid files", "warn");
    inputEl.value = "";
    return;
  }
  try {
    const d = await api("/api/refs/upload", { method: "POST", body: form });
    if (d?.ok && d.added > 0) {
      refreshRefs();
      showToast(`Added ${d.added}/${appended} image(s)`, "success");
    } else if (d?.ok && d.added === 0) {
      showToast(`Sent ${appended}, 0 added (full or duplicates?)`, "warn");
    } else {
      showToast(d?.error || "Upload failed", "error");
    }
  } catch (e) {
    showToast("Upload exception: " + (e.message || e), "error");
  }
  // Reset so picking the same file twice in a row still fires onchange.
  inputEl.value = "";
}

// Compatibility stub: any old caller (or a stale event wired up elsewhere)
// gets routed to the same hidden input. The native click is treated as a
// user gesture because it inherits from the original handler frame.
async function browseRefImages() {
  const inp = document.getElementById("refFileInput");
  if (inp) inp.click();
}
async function replaceRef(idx) {
  const d = await api("/api/browse-replace-ref", { method: "POST", body: { index: idx } });
  if (d.ok) { refreshRefs(); showToast("Reference replaced", "success"); }
}
async function removeRef(idx) {
  // Delete = empty slot `idx` on the server. refreshRefs() then re-syncs the
  // prompt: [Image idx+1] de-activates to @imageN text; every other slot
  // (and its mention) keeps its number. No shifting.
  await api(`/api/refs/${idx}`, { method: "DELETE" });
  await refreshRefs();
  scheduleSettingsSave();
}
async function togglePin(idx) { await api(`/api/refs/pin/${idx}`, { method: "POST" }); refreshRefs(); }
async function clearRefs(pp = true) {
  // Server empties non-pinned slots; refreshRefs() re-syncs the prompt
  // (cleared slots' [Image N] become @imageN text).
  await api("/api/refs/clear", { method: "POST", body: { preserve_pinned: pp } });
  await refreshRefs();
  scheduleSettingsSave();
}

// SWAP the contents of slot `from` and slot `to`. Dragging a ref cell onto
// another cell swaps exactly those two slots — the dragged image lands in the
// target slot, the target's old content moves to the dragged slot, and
// NOTHING ELSE MOVES. Dropping onto an empty slot is therefore a clean "move
// there" (the dragged slot becomes empty). This is NOT an insertion shift:
// dragging slot 4 onto slot 2 leaves slot 3 exactly where it was.
//
// The prompt text is not touched — refreshRefs -> syncMentionsToRefSlots
// re-resolves [Image N] <-> @imageN against the new slot-filled state.
async function reorderRefs(from, to) {
  const n = refSlotCount;
  if (from === to || from < 0 || from >= n || to < 0 || to >= n) return;
  // order[newPos] = oldSlotIndex — identity with `from` and `to` swapped.
  const order = [...Array(n).keys()];
  const tmp = order[from];
  order[from] = order[to];
  order[to] = tmp;
  const r = await api("/api/refs/reorder", { method: "POST", body: { order } });
  if (!r || !r.ok) { showToast((r && r.error) || "Move failed", "error"); return; }
  await refreshRefs();
  scheduleSettingsSave();
  showToast("Reference moved", "success");
}

// Counter avoids flashing the border when the cursor crosses a child element
let _refAreaDragDepth = 0;
function onRefDragEnter(e) {
  // A ref-cell reorder drag stays inside the grid — don't light up the whole
  // ref area as an "add here" drop zone for it.
  if (e.dataTransfer?.types.includes("application/x-nb-ref-reorder")) return;
  e.preventDefault();
  _refAreaDragDepth++;
  document.getElementById("refArea").classList.add("dragover");
}
function onRefDragLeave(e) {
  _refAreaDragDepth = Math.max(0, _refAreaDragDepth - 1);
  if (_refAreaDragDepth === 0) document.getElementById("refArea").classList.remove("dragover");
}
async function onRefDrop(e) {
  e.preventDefault();
  _refAreaDragDepth = 0;
  document.getElementById("refArea").classList.remove("dragover");

  // 0) Reorder drag dropped on empty ref-area space (not on a cell) — treat
  //    it as "move to the end". Drops onto a cell are handled by that cell's
  //    own drop listener, which stops propagation before reaching here.
  const reorderRaw = e.dataTransfer?.getData("application/x-nb-ref-reorder");
  if (reorderRaw !== "" && reorderRaw != null) {
    const from = parseInt(reorderRaw, 10);
    if (!Number.isNaN(from) && refSlotCount > 0) reorderRefs(from, refSlotCount - 1);
    return;
  }

  // 1) Internal drag from a gallery card
  const internalPath = e.dataTransfer?.getData("application/x-nb-gallery-path");
  if (internalPath) {
    const d = await api("/api/gallery/use-as-ref", { method: "POST", body: { filepath: internalPath } });
    if (d.ok) { refreshRefs(); showToast("Added as reference", "success"); }
    else { showToast(d.error || "Failed to add", "error"); }
    return;
  }

  // 2) Actual file blob(s) in dataTransfer.files — external file drag,
  //    clipboard-paste, or web image drag when Chromium pre-downloaded it.
  const files = e.dataTransfer?.files;
  if (files?.length) {
    const form = new FormData();
    for (const f of files) {
      const ext = f.name.split(".").pop().toLowerCase();
      if (["png", "jpg", "jpeg", "webp", "bmp"].includes(ext)) form.append("files", f);
    }
    if ([...form.entries()].length) {
      const d = await api("/api/refs/upload", { method: "POST", body: form });
      if (d.added > 0) { refreshRefs(); showToast(`Added ${d.added} image(s)`, "success"); }
      else { showToast("Slots are full — drop onto a slot to replace it", "warn"); }
      return;
    }
  }

  // 3) URL fallback — web image drag without a pre-downloaded blob.
  //    Chromium keeps the image URL in text/uri-list / text/html / text/plain.
  //    Fetch() from the renderer is blocked by CORS for most image hosts,
  //    so the server downloads it for us.
  const url = _extractImageUrlFromDrag(e.dataTransfer);
  if (url) {
    showToast("Downloading image...", "info");
    const d = await api("/api/refs/download-url", { method: "POST", body: { url } });
    if (d.ok) { refreshRefs(); showToast("Added from web", "success"); }
    else { showToast(d.error || "Download failed", "error"); }
    return;
  }

  showToast("No supported image in drop", "warn");
}

// Pull the first usable http(s) image URL out of a DataTransfer. Tries
// text/uri-list first (standard), then <img src="..."> from text/html,
// then text/plain as a last resort.
function _extractImageUrlFromDrag(dt) {
  if (!dt) return null;

  const uriList = dt.getData("text/uri-list");
  if (uriList) {
    for (const line of uriList.split(/\r?\n/)) {
      const s = line.trim();
      if (s && !s.startsWith("#") && /^https?:\/\//i.test(s)) return s;
    }
  }

  const html = dt.getData("text/html");
  if (html) {
    const m = html.match(/<img[^>]+src\s*=\s*["']([^"']+)["']/i);
    if (m && /^https?:\/\//i.test(m[1])) return m[1];
  }

  const plain = (dt.getData("text/plain") || "").trim();
  if (/^https?:\/\//i.test(plain)) return plain;

  return null;
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

    // Allow dragging the card onto the reference area to "Use as Ref".
    // Custom MIME type so onRefDrop can distinguish internal drags from
    // OS file drops (which use dataTransfer.files).
    card.draggable = true;
    card.addEventListener("dragstart", (e) => {
      try {
        e.dataTransfer.effectAllowed = "copy";
        e.dataTransfer.setData("application/x-nb-gallery-path", item.filepath);
        e.dataTransfer.setData("text/plain", item.filepath);
      } catch (_) {}
    });

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
    // Without this the <img>'s native drag hijacks the gesture: the browser
    // fires an image/uri-list drag with no custom data, so the card-level
    // dragstart (which stamps application/x-nb-gallery-path) effectively
    // never runs, and drops onto the ref area see nothing useful.
    img.draggable = false;
    img.src = `/api/gallery/thumb?path=${encodeURIComponent(item.filepath)}&size=${thumbSize}`;
    img.loading = "lazy";
    img.alt = item.filename || "";
    // Refine aspect from the actual image once loaded (handles legacy items
    // missing an `aspect` field).
    img.addEventListener("load", () => {
      // Measure the real ratio when the item has no usable "W:H" aspect —
      // legacy items (no aspect) AND new "auto" generations both land here.
      const hasNumericAspect = item.aspect && /^\d+:\d+$/.test(item.aspect);
      if (!hasNumericAspect && img.naturalWidth > 0 && img.naturalHeight > 0) {
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
    // 3-way: vertex -> [V] green, studio -> [S] yellow, openai -> [G] cyan.
    const badgeMap = {
      vertex: { cls: "vertex", text: " [V]" },
      studio: { cls: "studio", text: " [S]" },
      openai: { cls: "openai", text: " [G]" },
    };
    const bm = badgeMap[item.api_used] || badgeMap.studio;
    badge.className = "api-badge " + bm.cls;
    badge.textContent = bm.text;
    infoRight.appendChild(badge);
    infoRow.appendChild(infoRight);
    body.appendChild(infoRow);

    // Meta row
    const meta = document.createElement("div");
    meta.className = "card-meta";
    const parts = [];
    if (item.aspect === "custom") {
      // Custom: show the real output pixels + ratio (the "4K"/"custom" labels are vague).
      const gs = item.generation_settings || {};
      const cw = Number(gs.custom_w), ch = Number(gs.custom_h);
      if (cw > 0 && ch > 0) {
        const c = gpt2CustomSize(cw, ch);   // mirrors the server correction = actual output size
        parts.push(`${c.w}×${c.h}`);
        parts.push(`${(Math.max(c.w, c.h) / Math.min(c.w, c.h)).toFixed(2)}:1`);
      } else {
        parts.push("custom");
      }
    } else {
      if (item.resolution) parts.push(item.resolution);
      if (item.aspect) parts.push(item.aspect);
    }
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
  // Skip mid-composition — Hangul characters buffer up and trigger this on
  // every keystroke, hammering refreshGallery. Real filter runs on commit.
  if (_imeComposing) return;
  if (searchDebounce) clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => refreshGallery(), 250);
}
// Called on Korean IME compositionend — flush immediately.
function filterGalleryImmediate() {
  if (searchDebounce) clearTimeout(searchDebounce);
  refreshGallery();
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
  // A gallery image that was in use as a ref got deleted — the server already
  // emptied that ref slot. Just refresh: refreshRefs() -> syncMentionsToRefSlots
  // de-activates the now-empty slot's [Image N] mention to @imageN text. No
  // shifting; every other slot keeps its number.
  if (!removals || !removals.length) return;
  refreshRefs();
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

async function showPromptPopup(prompt, filename) {
  // Renamed behavior: instead of opening a popup window, copy the prompt
  // straight to the clipboard and surface a toast + server log line.
  const text = prompt || "";
  if (!text) {
    showToast("No prompt to copy", "warn");
    return;
  }
  let copied = false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      copied = true;
    }
  } catch (_) { /* fall through to execCommand */ }
  if (!copied) {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      copied = document.execCommand("copy");
      document.body.removeChild(ta);
    } catch (_) {}
  }
  if (copied) {
    showToast("Prompt copied", "success");
    // Best-effort log line so the Log pane shows the action too.
    try {
      await api("/api/log-message", { method: "POST", body: { message: `Prompt copied: ${filename || ""}` } });
    } catch (_) {}
  } else {
    showToast("Copy failed", "error");
  }
}

// ==========================================
// Generation
// ==========================================
async function generate() {
  // Last-chance sync: a user can type "@image1" and hit Enter without ever
  // blurring the box. Re-sync mentions to slot state now — before
  // saveSettings() reads the values — so the server snapshots "[Image N]"
  // for every filled slot.
  syncMentionsToRefSlots();
  await saveSettings();
  const d = await api("/api/generate", { method: "POST" });
  if (!d.ok) {
    showToast(d.error || "Cannot generate", "error");
    return;
  }
  // Fresh batch (not tacked onto an in-flight run): reset the progress bar
  // that the previous batch left pinned at 100%. Without this, the bar
  // stayed full and the "Completed" label never changed, so the second
  // and later runs looked frozen even though generation was fine.
  if (!d.queued) {
    const pf = document.getElementById("progressFill");
    if (pf) pf.style.width = "0%";
    const pl = document.getElementById("progressLabel");
    if (pl) pl.textContent = `Starting ${d.count} image(s)...`;
    const sl = document.getElementById("statusLabel");
    if (sl) sl.textContent = `Generating… 0/${d.count} done`;
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
let statusPollTimer = null;
function startPolling() {
  stopPolling();
  pollTimer = setInterval(pollEvents, 800);
  logPollTimer = setInterval(pollLogs, 2000);
  // Status poll carries close-requested + API status + generation state;
  // 500ms keeps close button feel instant.
  statusPollTimer = setInterval(refreshApiStatus, 500);
}
function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  if (logPollTimer) { clearInterval(logPollTimer); logPollTimer = null; }
  if (statusPollTimer) { clearInterval(statusPollTimer); statusPollTimer = null; }
}
// Kill timers before the page goes away so we don't get a flood of
// "failed to fetch" entries after the server window is gone.
window.addEventListener("pagehide", stopPolling);
window.addEventListener("beforeunload", stopPolling);

// Coalesce many image_done events into a single refreshGallery call per
// poll tick. A 100-image batch used to trigger 100 full /api/gallery fetches
// which made the grid stutter even on fast machines.
let _galleryRefreshPending = false;
function scheduleGalleryRefresh() {
  if (_galleryRefreshPending) return;
  _galleryRefreshPending = true;
  requestAnimationFrame(() => {
    _galleryRefreshPending = false;
    refreshGallery();
  });
}

async function pollEvents() {
  const d = await api("/api/events");
  if (!d.events?.length) return;
  let anyDone = false, anyFailed = false;
  let lastDone = 0, lastFailed = 0, lastTotal = 0, lastOutstanding;
  for (const ev of d.events) {
    if (ev.type === "image_done") {
      anyDone = true;
      lastDone = ev.done;
      lastTotal = ev.total;
      lastOutstanding = ev.outstanding;
      const sk = document.querySelector(".skeleton");
      if (sk) sk.remove();
    } else if (ev.type === "image_failed") {
      anyFailed = true;
      lastDone = ev.done || 0;
      lastFailed = ev.failed || 0;
      lastTotal = ev.total;
      lastOutstanding = ev.outstanding;
      const sk = document.querySelector(".skeleton");
      if (sk) sk.remove();
    } else if (ev.type === "update_status") {
      // Background check result. If an update is available, show the
      // in-page confirm dialog (frontend-owned, no Python MessageBox).
      if (ev.has_update) {
        showUpdateConfirmModal(ev.current, ev.remote);
      } else {
        const kind = ev.kind === "error" ? "warn" : "info";
        showToast(ev.message || "Update check complete", kind);
      }
    } else if (ev.type === "update_progress") {
      const pct = typeof ev.pct === "number" ? ev.pct : 0;
      const mb = ev.total ? `${(ev.done/1048576).toFixed(1)}/${(ev.total/1048576).toFixed(1)}MB` : "";
      const bar = document.getElementById("nbUpdateBar");
      const lbl = document.getElementById("nbUpdateLabel");
      const sub = document.getElementById("nbUpdateSub");
      if (bar) bar.style.width = pct + "%";
      if (lbl) lbl.textContent = pct >= 100 ? "설치 준비 중…" : "다운로드 중…";
      if (sub) sub.textContent = pct >= 100 ? "곧 재시작됩니다" : `${pct}% · ${mb}`;
    } else if (ev.type === "update_swap") {
      const lbl = document.getElementById("nbUpdateLabel");
      const sub = document.getElementById("nbUpdateSub");
      if (ev.phase === "failed") {
        hideUpdateOverlay();
        showToast(ev.message || "업데이트 실패", "error");
      } else if (ev.phase === "noop") {
        hideUpdateOverlay();
        showToast(ev.message || "이미 최신입니다", "info");
      } else if (lbl && sub) {
        if (ev.phase === "handing_off") {
          lbl.textContent = "설치 준비 중…";
          sub.textContent = ev.message || "곧 재시작됩니다";
        }
      }
    } else if (ev.type === "done") {
      document.querySelectorAll(".skeleton").forEach(s => s.remove());
      updateGenUI(false, 0);
      scheduleGalleryRefresh();
      document.getElementById("progressLabel").textContent = `Done  ok ${ev.done || 0}  fail ${ev.failed || 0}`;
      document.getElementById("progressFill").style.width = (ev.done || 0) > 0 ? "100%" : "0%";
      document.getElementById("statusLabel").textContent = `Completed  ${ev.done || 0} image(s) saved`;
      return;
    }
  }
  if (anyDone || anyFailed) {
    scheduleGalleryRefresh();
    updateProgress(lastDone + lastFailed, lastTotal, lastOutstanding);
    if (typeof lastOutstanding === "number") updateGenUI(true, lastOutstanding);
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
  const openaiDot = document.getElementById("openaiDot");
  if (openaiDot) openaiDot.className = "dot " + (d.openai || "disconnected");
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

// Pending save name while the conflict modal is open (so the user's choice
// knows what name to retry with).
let _pendingSaveName = "";
let _pendingSuggestedName = "";

async function confirmSaveProject() {
  const name = document.getElementById("saveProjectName").value.trim();
  closeSaveModal();
  const d = await api("/api/project/save", { method: "POST", body: { name } });
  if (d.ok) {
    showToast(`Saved: ${d.name || "project"}`, "success");
    return;
  }
  if (d.conflict) {
    // Server found an existing file with the same name (not the currently
    // loaded project). Ask the user what to do instead of silently clobbering.
    _pendingSaveName = name;
    _pendingSuggestedName = d.suggested || (name + "_2");
    document.getElementById("saveConflictDesc").textContent =
      `"${d.existing_name}"이(가) 이미 저장 폴더에 있습니다. 덮어쓸까요, 아니면 "${_pendingSuggestedName}.json"으로 저장할까요?`;
    document.getElementById("saveConflictSuffixBtn").textContent =
      `"${_pendingSuggestedName}"(으)로 저장`;
    document.getElementById("saveConflictModal").classList.remove("hidden");
    return;
  }
  showToast(d.error || "Save failed", "error");
}

function closeSaveConflict() {
  document.getElementById("saveConflictModal").classList.add("hidden");
  _pendingSaveName = "";
  _pendingSuggestedName = "";
}

async function confirmSaveConflict(strategy) {
  const name = _pendingSaveName;
  closeSaveConflict();
  if (!name) return;
  const d = await api("/api/project/save", { method: "POST", body: { name, strategy } });
  if (d.ok) {
    showToast(`Saved: ${d.name || "project"}`, "success");
  } else {
    showToast(d.error || "Save failed", "error");
  }
}

async function loadProject() {
  // Show the Recent Projects modal instead of a file dialog — recent list
  // is more useful than navigating the filesystem. "Open Other JSON" link
  // in the modal still falls through to the OS file dialog.
  let projects = [];
  try {
    const d = await api("/api/project/recent");
    projects = d.projects || [];
  } catch (e) {}
  if (projects.length === 0) {
    const d = await api("/api/browse-project", { method: "POST" });
    if (d.ok) {
      await loadSettings();
      refreshGallery();
      refreshRefs();
      showToast("Project loaded", "success");
    }
    return;
  }
  document.getElementById("projectsModalTitle").textContent = "프로젝트 열기";
  document.getElementById("projectsModalDesc").textContent =
    "최근 프로젝트에서 선택하거나 다른 위치의 JSON 파일을 열 수 있어요.";
  document.getElementById("projectsModalCancelBtn").textContent = "취소";
  showProjectsModal(projects);
}

// ==========================================
// Recent Projects Picker
// ==========================================
async function checkRecentProjects() {
  if (allGalleryPaths.length > 0) return;
  const d = await api("/api/project/recent");
  if (d.projects?.length) {
    // First launch: offer to restore recent, else start blank
    document.getElementById("projectsModalTitle").textContent = "최근 프로젝트를 여시겠어요?";
    document.getElementById("projectsModalDesc").textContent =
      "이전에 작업하던 프로젝트에서 이어갈 수 있어요. 처음부터 시작하려면 '빈 프로젝트로 시작'을 누르세요.";
    document.getElementById("projectsModalCancelBtn").textContent = "빈 프로젝트로 시작";
    showProjectsModal(d.projects);
  }
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

async function loadProjectFromBrowser() {
  // Open the OS file dialog directly. Pre-v2201 this delegated to
  // loadProject(), which re-runs the recent-projects check and — if
  // recents exist (which they always do when this button is visible) —
  // just pops the same modal back up. Button looked completely dead.
  closeProjectsModal();
  const d = await api("/api/browse-project", { method: "POST" });
  if (d.ok) {
    await loadSettings();
    refreshGallery();
    refreshRefs();
    showToast("Project loaded", "success");
  }
}

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
["aspectSelect", "resolutionSelect", "countSelect", "qualitySelect", "namingSwitch",
 "namingPrefix", "namingDelimiter", "namingIndexPrefix", "namingPadding"].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener("change", () => saveSettings());
});

// Wire the Custom pixel inputs (ratio-lock, presets, ref-grab, live preview).
initCustomSize();

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
