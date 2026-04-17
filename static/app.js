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
        <button id="nbUpdateLater" style="background:#3A3A3C;color:#F5F5F7;border:none;border-radius:8px;height:34px;padding:0 16px;font-size:12px;font-weight:600;cursor:pointer">나중에</button>
        <button id="nbUpdateNow" style="background:#D4A574;color:#1C1C1E;border:none;border-radius:8px;height:34px;padding:0 18px;font-size:12px;font-weight:700;cursor:pointer">지금 설치</button>
      </div>
    </div>`;
  document.body.appendChild(w);
  document.getElementById("nbUpdateLater").addEventListener("click", () => {
    w.remove();
    _updateConfirmShown = false;
  });
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
  const r = await api("/api/settings", { method: "POST", body: {
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
  if (r && r.ok === false) {
    // Surface the failure — silent loss of settings is how users end up
    // fighting the UI for 10 minutes before noticing.
    showToast(r.error || "Settings save failed", "error");
  }
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
  ta.placeholder = "Describe the image... Enter=Generate, Shift+Enter=newline, @ mentions refs";
  ta.value = initialText;
  ta.addEventListener("input", (e) => {
    onPromptInput(e, ta);
    syncPromptHighlight(ta);
    scheduleSettingsSave();
  });
  ta.addEventListener("keyup", () => _tryShowMention(ta));
  ta.addEventListener("scroll", () => syncPromptHighlight(ta));
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

// rAF-coalesced highlight update — at most one DOM rewrite per frame,
// even if user types very fast (Korean IME emits many events per second).
const _pendingHighlights = new WeakSet();
function syncPromptHighlight(textarea) {
  if (_pendingHighlights.has(textarea)) return;
  _pendingHighlights.add(textarea);
  requestAnimationFrame(() => {
    _pendingHighlights.delete(textarea);
    const wrap = textarea.closest(".prompt-wrap");
    if (!wrap) return;
    const hl = wrap.querySelector(".prompt-highlight");
    if (!hl) return;
    hl.innerHTML = _buildHighlightedHTML(textarea.value);
    hl.scrollTop = textarea.scrollTop;
  });
}

function setupFixedPromptMention() {
  const fp = document.getElementById("fixedPrompt");
  if (fp) {
    fp.addEventListener("input", (e) => {
      onPromptInput(e, fp);
      syncPromptHighlight(fp);
      scheduleSettingsSave();
    });
    fp.addEventListener("keyup", () => _tryShowMention(fp));
    fp.addEventListener("scroll", () => syncPromptHighlight(fp));
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
  document.getElementById("modelSelect").value = "gemini-3-pro-image-preview";
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
  if (pos > 0 && textarea.value[pos - 1] === "@" && refCount > 0) {
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

  // Enter (without Shift) → queue a generation.
  // Shift+Enter keeps the default newline behavior.
  // When the mention menu is open, Enter still inserts the mention (handled below).
  if (e.key === "Enter" && !e.shiftKey && !mentionMenu && !_imeComposing) {
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

  const menuH = mentionMenu.offsetHeight || (refCount * 30 + 10);
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
  const tag = `[Image ${parseInt(mentionMenu.dataset.selected || "0") + 1}]`;
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

    // Drag-and-drop file onto cell to replace this ref.
    // Counter pattern so dragleave firing on child elements (image, buttons)
    // doesn't flash the drop-target highlight off and back on.
    let _dragDepth = 0;
    cell.addEventListener("dragenter", (e) => {
      e.preventDefault();
      _dragDepth++;
      cell.classList.add("drop-target");
    });
    cell.addEventListener("dragover", (e) => { e.preventDefault(); });
    cell.addEventListener("dragleave", () => {
      _dragDepth = Math.max(0, _dragDepth - 1);
      if (_dragDepth === 0) cell.classList.remove("drop-target");
    });
    cell.addEventListener("drop", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      _dragDepth = 0;
      cell.classList.remove("drop-target");
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

// Counter avoids flashing the border when the cursor crosses a child element
let _refAreaDragDepth = 0;
function onRefDragEnter(e) {
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
