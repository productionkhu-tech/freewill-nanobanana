# -*- coding: utf-8 -*-
"""
NanoBanana Web — AI Image Studio (Flask + Selenium)
Converted from customtkinter to web-based interface.
All original features preserved.
"""

import os
import io
import sys
import json
import re
import subprocess

import time
import random
import threading
import base64
import atexit
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait

from flask import Flask, render_template, request, jsonify, send_file, Response
from PIL import Image, ImageGrab
from google import genai
from google.genai import types


def _to_display_image(img):
    """Normalize a PIL image for in-app display / storage while PRESERVING
    the alpha channel if present. Used for reference images and generated
    images — both go through endpoints that can serve PNG (alpha-capable).

    - P (palette) mode with transparency → RGBA
    - LA (luminance + alpha) → RGBA (so the rest of the code only deals with
      RGB / RGBA)
    - RGBA / RGB → cloned (never return the caller's original object, since
      callers typically use `with Image.open()` which closes the source on
      exit and we'd be left holding a closed handle)
    - anything else (CMYK, I, F, etc.) → RGB (alpha isn't meaningful there)

    This replaces the old _to_rgb_flatten for display paths. Flattening
    transparent pixels to white looked wrong for PNG logos/icons — the user
    expects a cutout over the dark UI background, not a white halo.
    """
    try:
        if img.mode == "P":
            # Palette mode may have transparency — expanding to RGBA preserves it
            img = img.convert("RGBA")
        if img.mode == "LA":
            img = img.convert("RGBA")
        if img.mode == "RGBA":
            return img.copy()
        if img.mode == "RGB":
            return img.copy()
        return img.convert("RGB")
    except Exception:
        return img.convert("RGB")


def _to_rgb_flatten(img, bg_color=(255, 255, 255)):
    """Same as _to_display_image but collapses any alpha onto bg_color.
    Only use this for encoders that can't carry alpha (JPEG, BMP) — for
    display/PNG paths use _to_display_image so transparency is preserved.
    """
    try:
        if img.mode == "P":
            img = img.convert("RGBA")
        if img.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", img.size, bg_color)
            alpha = img.split()[-1]
            bg.paste(img, mask=alpha)
            return bg
        if img.mode != "RGB":
            return img.convert("RGB")
        return img.copy()
    except Exception:
        return img.convert("RGB")

# ==========================================
# API Credentials — read from environment variables
# Run setup_env.bat to configure these.
# ==========================================

# ==========================================
# Rate Limiter
# ==========================================
class RateLimiter:
    def __init__(self, interval=7.0, capacity=1.0):
        self.interval = max(0.1, float(interval))
        self.capacity = max(1.0, float(capacity))
        self.tokens = self.capacity
        self.refill_rate = self.capacity / self.interval
        self.lock = threading.Lock()
        self.updated_at = time.time()

    def _refill_locked(self):
        now = time.time()
        elapsed = max(0.0, now - self.updated_at)
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.updated_at = now

    def acquire(self, should_cancel=None, sleep_step=0.1):
        while True:
            with self.lock:
                self._refill_locked()
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
                deficit = 1.0 - self.tokens
                w = deficit / self.refill_rate
            while w > 0:
                if should_cancel and should_cancel():
                    return False
                chunk = min(sleep_step, w)
                time.sleep(chunk)
                w -= chunk


# ==========================================
# Application State (singleton)
# ==========================================
class AppState:
    def __init__(self):
        self.client_vertex = None
        self.client_studio = None
        # Rate limit: UI hint says "10 RPM auto-throttled to ~8 RPM". That's
        # 1 request every 7.5s per provider. Previously this was 0.5s (120
        # RPM) — we'd hit 429s constantly.
        self.vertex_rate_limiter = RateLimiter(interval=7.5)
        self.studio_rate_limiter = RateLimiter(interval=7.5)
        self.is_generating = False
        self.cancel_flag = False
        self.done_count = 0
        self.fail_count = 0
        self.discarded_count = 0
        self.queue_count = 0
        self.max_queued_images = 100
        self.max_parallel_requests = 100
        self.pending_jobs = []
        self.pending_jobs_lock = threading.Lock()
        self.active_job_count = 0
        # Persisted across restarts (loaded below from .nanobanana/prefs.json)
        self.skip_delete_confirm = False
        # Always-on-top window state. Actual enforcement is done via Win32
        # SetWindowPos on the NanoBanana HWND by the toggle endpoint; this
        # flag just remembers the user's choice so we can reapply after a
        # page reload or a minimize/restore cycle.
        self.always_on_top = False
        self.output_dir = os.path.join(os.path.expanduser("~"), "Desktop", "NanoBanana_Output")
        self.file_counter = 0
        self.file_counter_lock = threading.Lock()   # parallel-worker safe naming
        # Protect the parallel lists ref_images/ref_path_list/ref_pinned
        self.ref_lock = threading.RLock()
        # Protect gallery_items mutations vs HTTP thread iteration
        self.gallery_lock = threading.RLock()
        # Prompt history (last N entries, persisted)
        self.prompt_history = []
        self.max_prompt_history = 50

        # Reference images
        self.ref_images = []       # PIL.Image list
        self.ref_path_list = []    # file paths
        self.ref_pinned = []       # pin status

        # Gallery
        self.gallery_items = {}    # filepath -> item dict
        self.generated_paths = []
        self.favorites = set()
        self.gallery_order_counter = 0
        self.gallery_columns = 2

        # Settings
        self.model = "gemini-3-pro-image-preview"
        self.aspect = "16:9"
        self.resolution = "4K"
        self.count = 1
        self.fixed_prompt = ""
        self.prompt_sections = [""]
        self.naming_enabled = False
        self.naming_prefix = "S010"
        self.naming_delimiter = "C010"
        self.naming_index_prefix = "I"
        self.naming_padding = 3

        # Project
        self.current_project_path = None
        self.project_dirty = False
        self.project_default_save_dir = os.path.join(
            os.path.expanduser("~/Documents"),
            "NanoBanana JSON",
        )

        # Logs
        self.logs = []
        self.log_lock = threading.Lock()

        # Temp refs
        self.temp_ref_dir = os.path.join(
            os.path.expanduser("~/Pictures"),
            "Screenshots",
            "NanoBanana Clipboard",
        )
        self.temp_ref_paths = set()

        # API status
        self.vertex_status = "disconnected"
        self.studio_status = "disconnected"
        self.vertex_credentials_path = None
        self.vertex_session_disabled = False

        # Generation progress events
        self.progress_events = []
        self.progress_lock = threading.Lock()

        # Close-requested flag (set by launcher when user clicks X)
        self.close_requested = False

        # Throttle for incremental project auto-save during long batches
        self._last_autosave_ts = 0.0

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        with self.log_lock:
            self.logs.append(entry)
            if len(self.logs) > 2000:
                self.logs = self.logs[-1000:]

    def push_event(self, event):
        with self.progress_lock:
            self.progress_events.append(event)

    def pop_events(self):
        with self.progress_lock:
            events = list(self.progress_events)
            self.progress_events.clear()
            return events

    # --- Persisted preferences (skip_delete_confirm, prompt_history) ---
    def _prefs_file(self):
        d = os.path.join(os.path.expanduser("~"), ".nanobanana")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        return os.path.join(d, "prefs.json")

    def load_prefs(self):
        try:
            with open(self._prefs_file(), "r", encoding="utf-8") as f:
                data = json.load(f)
            self.skip_delete_confirm = bool(data.get("skip_delete_confirm", False))
            hist = data.get("prompt_history", [])
            if isinstance(hist, list):
                self.prompt_history = [str(x) for x in hist][: self.max_prompt_history]
        except Exception:
            pass

    def save_prefs(self):
        try:
            tmp = self._prefs_file() + ".tmp"
            data = {
                "skip_delete_confirm": self.skip_delete_confirm,
                "prompt_history": self.prompt_history,
            }
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._prefs_file())
        except Exception as e:
            self.log(f"prefs save failed: {str(e)[:80]}")

    def push_prompt_history(self, prompt):
        p = (prompt or "").strip()
        if not p:
            return
        # Move to front, dedupe
        self.prompt_history = [p] + [x for x in self.prompt_history if x != p]
        self.prompt_history = self.prompt_history[: self.max_prompt_history]
        self.save_prefs()

    # --- API ---
    def cleanup_vertex_credentials(self):
        pass

    def init_api(self):
        # Load persisted preferences first so the rest of the app sees them
        self.load_prefs()
        # One-time cleanup: delete any orphaned .meta.json sidecars in the
        # output folder. Pre-v1723 builds wrote one next to every generated
        # image but nothing ever read them back, and they didn't get removed
        # when the user deleted the image. Users ended up with a folder full
        # of .meta.json clutter. We only touch files that:
        #   1. end with .meta.json
        #   2. sit next to an image that no longer exists (true orphan), OR
        #      next to an image we generated (same .png.meta.json pattern)
        try:
            if os.path.isdir(self.output_dir):
                for name in os.listdir(self.output_dir):
                    if not name.endswith(".meta.json"):
                        continue
                    full = os.path.join(self.output_dir, name)
                    # Strip the .meta.json suffix to find the supposed parent image
                    try:
                        os.remove(full)
                    except Exception:
                        pass
        except Exception:
            pass
        # Vertex AI — requires GOOGLE_APPLICATION_CREDENTIALS + NANOBANANA_PROJECT_ID
        creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
        project_id = os.environ.get("NANOBANANA_PROJECT_ID", "")
        location = os.environ.get("NANOBANANA_LOCATION", "global")

        if creds_path and os.path.isfile(creds_path) and project_id:
            try:
                self.client_vertex = genai.Client(
                    vertexai=True, project=project_id, location=location
                )
                self.log("Vertex AI connected")
                self.vertex_status = "connected"
            except Exception as e:
                self.log(f"Vertex error: {e}")
                self.vertex_status = "error"
        else:
            self.log("Vertex AI: credentials not configured (skipped)")
            self.vertex_status = "disconnected"

        # AI Studio — requires NANOBANANA_STUDIO_KEY
        studio_key = os.environ.get("NANOBANANA_STUDIO_KEY", "")
        if studio_key:
            try:
                self.client_studio = genai.Client(api_key=studio_key)
                self.log("AI Studio connected")
                self.studio_status = "connected"
            except Exception as e:
                self.log(f"Studio error: {e}")
                self.studio_status = "error"
        else:
            self.log("AI Studio: key not configured (skipped)")
            self.studio_status = "disconnected"

    # --- Provider helpers ---
    def get_available_providers(self):
        providers = []
        if self.client_studio:
            providers.append("studio")
        if self.client_vertex and not self.vertex_session_disabled:
            providers.append("vertex")
        return providers

    def get_provider_client(self, provider):
        if provider == "vertex":
            return self.client_vertex
        if provider == "studio":
            return self.client_studio
        return None

    def get_provider_limiter(self, provider):
        if provider == "vertex":
            return self.vertex_rate_limiter
        if provider == "studio":
            return self.studio_rate_limiter
        return None

    def get_provider_label(self, provider):
        return "Vertex" if provider == "vertex" else "Studio"

    def build_provider_order(self, preferred_provider=None):
        providers = self.get_available_providers()
        if preferred_provider in providers:
            return [preferred_provider] + [p for p in providers if p != preferred_provider]
        return providers

    def disable_vertex_for_session(self, reason=""):
        if self.vertex_session_disabled or not self.client_studio:
            return
        self.vertex_session_disabled = True
        self.log(f"Vertex disabled for this session ({reason})" if reason else "Vertex disabled for this session")

    def is_retryable_error(self, err):
        err_lower = err.lower()
        markers = (
            "429", "resource_exhausted", "timeout", "timed out",
            "deadline exceeded", "500", "502", "503", "504",
            "internal server error", "server error", "service unavailable",
            "temporarily unavailable", "bad gateway", "gateway timeout",
            "connection reset", "connection aborted", "connection refused",
            "remote disconnected", "network error", "connection error",
        )
        return any(m in err_lower for m in markers)

    def should_fallback(self, provider, err_text):
        err_lower = err_text.lower()
        if provider == "studio":
            return self.is_retryable_error(err_text)
        if provider == "vertex":
            return "invalid_grant" in err_lower or self.is_retryable_error(err_text)
        return False

    def call_api(self, model, contents, config, preferred_provider=None):
        errors = []
        providers = self.build_provider_order(preferred_provider)
        for i, provider in enumerate(providers):
            client = self.get_provider_client(provider)
            if not client:
                continue
            limiter = self.get_provider_limiter(provider)
            if limiter and not limiter.acquire(should_cancel=lambda: self.cancel_flag):
                raise RuntimeError("Cancelled")
            label = self.get_provider_label(provider)
            try:
                self.log(f"{label} requesting...")
                t = time.time()
                resp = client.models.generate_content(model=model, contents=contents, config=config)
                self.log(f"{label} OK ({time.time()-t:.1f}s)")
                return resp, provider
            except Exception as e:
                err_text = str(e)
                self.log(f"{label} failed ({time.time()-t:.1f}s): {err_text[:80]}")
                errors.append(f"{provider}: {err_text[:120]}")
                if provider == "vertex" and "invalid_grant" in err_text.lower():
                    self.disable_vertex_for_session("invalid_grant")
                if i < len(providers) - 1 and self.should_fallback(provider, err_text):
                    next_p = providers[i + 1]
                    self.log(f"-> {self.get_provider_label(next_p)} fallback")
                    continue
                break
        raise RuntimeError(f"All providers failed: {'; '.join(errors)}")

    def extract_image_from_response(self, resp):
        if not (resp.candidates and resp.candidates[0].content and resp.candidates[0].content.parts):
            return None
        for part in resp.candidates[0].content.parts:
            if hasattr(part, "inline_data") and part.inline_data and part.inline_data.data:
                # Preserve alpha if the model ever returns RGBA output.
                return _to_display_image(Image.open(io.BytesIO(part.inline_data.data)))
        return None

    def diagnose_empty_response(self, resp, provider_label=""):
        """Log WHY no image came back — finish_reason, safety_ratings, text parts,
        prompt_feedback. Helps the user understand safety filter vs text-only
        responses vs model refusal."""
        try:
            prefix = f"[{provider_label}] " if provider_label else ""
            # Prompt feedback (block reason at prompt level)
            pf = getattr(resp, "prompt_feedback", None)
            if pf is not None:
                block_reason = getattr(pf, "block_reason", None)
                if block_reason:
                    self.log(f"{prefix}PROMPT BLOCKED: {block_reason}")
                pf_ratings = getattr(pf, "safety_ratings", None) or []
                for r in pf_ratings:
                    cat = getattr(r, "category", "?")
                    prob = getattr(r, "probability", "?")
                    if str(prob) not in ("NEGLIGIBLE", "HarmProbability.NEGLIGIBLE"):
                        self.log(f"{prefix}prompt safety: {cat} = {prob}")

            if not resp.candidates:
                self.log(f"{prefix}Response has 0 candidates")
                return

            for i, cand in enumerate(resp.candidates):
                fr = getattr(cand, "finish_reason", None)
                if fr is not None:
                    self.log(f"{prefix}candidate[{i}] finish_reason: {fr}")
                ratings = getattr(cand, "safety_ratings", None) or []
                for r in ratings:
                    cat = getattr(r, "category", "?")
                    prob = getattr(r, "probability", "?")
                    blocked = getattr(r, "blocked", False)
                    if blocked or str(prob) not in ("NEGLIGIBLE", "HarmProbability.NEGLIGIBLE"):
                        flag = " [BLOCKED]" if blocked else ""
                        self.log(f"{prefix}safety: {cat} = {prob}{flag}")

                # Any text response (the model explaining WHY it refused)
                content = getattr(cand, "content", None)
                if content:
                    parts = getattr(content, "parts", None) or []
                    for p in parts:
                        txt = getattr(p, "text", None)
                        if txt:
                            snippet = txt.strip().replace("\n", " ")
                            if len(snippet) > 200:
                                snippet = snippet[:200] + "..."
                            self.log(f"{prefix}text reply: {snippet}")
        except Exception as e:
            self.log(f"diagnose error: {str(e)[:80]}")

    # --- Reference Images ---
    def get_ref_limit(self, model=None):
        m = model or self.model
        return 3 if m == "gemini-2.5-flash-image" else 14

    def get_effective_ref_images(self, model=None):
        limit = self.get_ref_limit(model)
        with self.ref_lock:
            return list(self.ref_images[:limit])

    def get_effective_ref_paths(self, model=None):
        limit = self.get_ref_limit(model)
        with self.ref_lock:
            return list(self.ref_path_list[:limit])

    def ref_image_to_bytes(self, ref_pil):
        buf = io.BytesIO()
        ref_pil.save(buf, format="PNG")
        return buf.getvalue()

    def ref_bytes_to_part(self, ref_data):
        return types.Part.from_bytes(data=ref_data, mime_type="image/png")

    def build_user_parts(self, prompt, ref_payloads):
        if not ref_payloads:
            return [types.Part.from_text(text=prompt)]
        matches = list(re.finditer(r"\[Image (\d+)\]", prompt))
        if not matches:
            parts = [types.Part.from_text(text=prompt)]
            for rd in ref_payloads:
                parts.append(self.ref_bytes_to_part(rd))
            return parts
        parts = []
        last_end = 0
        used = set()
        for m in matches:
            s, e = m.span()
            if s > last_end:
                t = prompt[last_end:s]
                if t:
                    parts.append(types.Part.from_text(text=t))
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(ref_payloads):
                parts.append(self.ref_bytes_to_part(ref_payloads[idx]))
                used.add(idx)
            else:
                parts.append(types.Part.from_text(text=m.group(0)))
            last_end = e
        if last_end < len(prompt):
            tail = prompt[last_end:]
            if tail:
                parts.append(types.Part.from_text(text=tail))
        for i, rd in enumerate(ref_payloads):
            if i not in used:
                parts.append(self.ref_bytes_to_part(rd))
        return parts or [types.Part.from_text(text=prompt)]

    def add_ref_image(self, filepath, pinned=False):
        with self.ref_lock:
            if filepath in self.ref_path_list:
                self.log(f"Ref already added: {os.path.basename(filepath)}")
                return False
            limit = self.get_ref_limit()
            if len(self.ref_images) >= limit:
                self.log(f"Max {limit} reference images")
                return False
            try:
                with Image.open(filepath) as img:
                    # Preserve alpha — PNG logos/icons should stay as
                    # cutouts, not get a white halo.
                    pil = _to_display_image(img)
                self.ref_images.append(pil)
                self.ref_path_list.append(filepath)
                self.ref_pinned.append(bool(pinned))
                self.project_dirty = True
                return True
            except Exception as e:
                self.log(f"Ref load failed: {str(e)[:80]}")
                return False

    def remove_ref(self, idx):
        with self.ref_lock:
            if 0 <= idx < len(self.ref_images):
                img = self.ref_images.pop(idx)
                fp = self.ref_path_list.pop(idx)
                if idx < len(self.ref_pinned):
                    self.ref_pinned.pop(idx)
                try:
                    img.close()
                except Exception:
                    pass
                self.cleanup_temp_ref_path(fp)
                self.project_dirty = True
                return True
            return False

    def replace_ref(self, idx, filepath):
        """Replace the ref at `idx` with the image at `filepath`, preserving
        position and pin state. Used by drag-drop onto an existing ref cell."""
        with self.ref_lock:
            if not (0 <= idx < len(self.ref_images)):
                return False
            try:
                with Image.open(filepath) as img:
                    pil = _to_display_image(img)
            except Exception as e:
                self.log(f"Replace failed: {str(e)[:80]}")
                return False
            old = self.ref_images[idx]
            old_fp = self.ref_path_list[idx]
            self.ref_images[idx] = pil
            self.ref_path_list[idx] = filepath
            try: old.close()
            except Exception: pass
            self.cleanup_temp_ref_path(old_fp)
            self.project_dirty = True
            return True

    def toggle_ref_pin(self, idx):
        with self.ref_lock:
            if not (0 <= idx < len(self.ref_path_list)):
                return
            while len(self.ref_pinned) < len(self.ref_path_list):
                self.ref_pinned.append(False)
            self.ref_pinned[idx] = not self.ref_pinned[idx]
            self.project_dirty = True

    def clear_refs(self, preserve_pinned=False):
        kept_imgs, kept_paths, kept_pinned = [], [], []
        removed = []
        with self.ref_lock:
            for i, (img, fp) in enumerate(zip(self.ref_images, self.ref_path_list)):
                pin = i < len(self.ref_pinned) and bool(self.ref_pinned[i])
                if preserve_pinned and pin:
                    kept_imgs.append(img)
                    kept_paths.append(fp)
                    kept_pinned.append(pin)
                else:
                    removed.append(fp)
                    try:
                        img.close()
                    except Exception:
                        pass
            self.ref_images = kept_imgs
            self.ref_path_list = kept_paths
            self.ref_pinned = kept_pinned
            self.project_dirty = True
        for fp in removed:
            self.cleanup_temp_ref_path(fp)

    def cleanup_temp_ref_path(self, filepath):
        if filepath not in self.temp_ref_paths:
            return
        self.temp_ref_paths.discard(filepath)
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass

    def paste_clipboard_ref(self):
        if sys.platform != "win32":
            return False, "Clipboard paste only on Windows"
        try:
            clip = ImageGrab.grabclipboard()
        except Exception:
            return False, "Failed to read clipboard"

        if isinstance(clip, Image.Image):
            os.makedirs(self.temp_ref_dir, exist_ok=True)
            fn = f"clipboard_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
            fp = os.path.join(self.temp_ref_dir, fn)
            # PNG supports alpha — preserve clipboard transparency if any
            _to_display_image(clip).save(fp, "PNG")
            self.temp_ref_paths.add(fp)
            self.add_ref_image(fp)
            return True, "Pasted image as reference"

        if isinstance(clip, list):
            added = 0
            for fp in clip:
                if not isinstance(fp, str) or not os.path.exists(fp):
                    continue
                ext = os.path.splitext(fp)[1].lower()
                if ext not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                    continue
                self.add_ref_image(fp)
                added += 1
            if added:
                return True, f"Pasted {added} image(s) as reference"
        return False, "No image in clipboard"

    # --- Prompt ---
    def compose_prompt(self):
        chunks = []
        if self.fixed_prompt:
            chunks.append(self.fixed_prompt)
        for s in self.prompt_sections:
            if s:
                chunks.append(s)
        return "\n\n".join(chunks).strip()

    # --- Naming ---
    def get_naming_settings(self):
        return {
            "enabled": self.naming_enabled,
            "prefix": self.naming_prefix or "image",
            "delimiter": self.naming_delimiter or "",
            "index_prefix": self.naming_index_prefix or "",
            "padding": max(1, min(5, self.naming_padding)),
        }

    def make_filename(self, seed, naming=None):
        s = naming or self.get_naming_settings()
        if s["enabled"]:
            # Atomic read-increment-use under a lock. Without this, two
            # parallel workers could read the same counter value and write
            # identical filenames — silent image overwrite.
            with self.file_counter_lock:
                self.file_counter += 1
                n = self.file_counter
            num = str(n).zfill(s["padding"])
            prefix = (s["prefix"] or "image").strip()
            middle = (s["delimiter"] or "").strip()
            idx_prefix = (s.get("index_prefix") or "").strip()
            number_part = f"{idx_prefix}{num}" if idx_prefix else num
            if middle:
                return f"{prefix}_{middle}_{number_part}.png"
            return f"{prefix}_{number_part}.png"
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"nano_{ts}_{seed}.png"

    def prepare_file_counter(self, naming=None):
        s = naming or self.get_naming_settings()
        if not s["enabled"]:
            with self.file_counter_lock:
                self.file_counter = 0
            return
        # Relaxed pattern — match ANY number-ending filename that starts with
        # the prefix, so legacy naming schemes don't reset the counter and
        # cause overwrites.
        strict_pattern = re.compile(
            rf"^{re.escape(s['prefix'])}"
            rf"(?:_{re.escape(s['delimiter'])})?"
            rf"_{re.escape(s.get('index_prefix', ''))}(\d+)\.png$",
            re.IGNORECASE,
        )
        loose_pattern = re.compile(
            rf"^{re.escape(s['prefix'])}.*?(\d+)\.png$",
            re.IGNORECASE,
        )
        max_num = 0
        try:
            for name in os.listdir(self.output_dir):
                m = strict_pattern.match(name) or loose_pattern.match(name)
                if m:
                    try:
                        max_num = max(max_num, int(m.group(1)))
                    except (ValueError, IndexError):
                        continue
        except OSError:
            pass
        with self.file_counter_lock:
            self.file_counter = max_num

    def build_png_metadata(self, prompt, model):
        from PIL.PngImagePlugin import PngInfo
        prompt_text = prompt or ""
        prompt_line = " ".join(prompt_text.split())
        comment = f"Prompt: {prompt_text}\nModel: {model}"
        metadata = PngInfo()
        for k, v in (
            ("Title", prompt_line[:255]), ("Description", prompt_text),
            ("Comment", comment), ("Software", "NanoBanana"),
            ("Source", "NanoBanana"), ("Model", model), ("Prompt", prompt_text),
        ):
            if v:
                try:
                    metadata.add_text(k, v)
                except Exception:
                    pass
        exif_bytes = None
        try:
            exif = Image.Exif()
            exif[270] = prompt_text
            exif[272] = model
            exif[305] = "NanoBanana"
            exif[315] = "NanoBanana"
            exif_bytes = exif.tobytes()
        except Exception:
            pass
        return metadata, exif_bytes

    def save_generated_image(self, pil_img, filepath, prompt, model):
        metadata, exif_bytes = self.build_png_metadata(prompt, model)
        kw = {"pnginfo": metadata}
        if exif_bytes:
            kw["exif"] = exif_bytes
        pil_img.save(filepath, "PNG", **kw)

    # --- Gallery ---
    def add_gallery_item(self, filepath, prompt, elapsed, api_used, aspect="", resolution="", generated_at="", generation_settings=None):
        # Lock to prevent dict-size-change errors when HTTP threads iterate
        # gallery_items concurrently with the worker adding items.
        with self.gallery_lock:
            self.gallery_order_counter -= 1
            self.gallery_items[filepath] = {
                "filepath": filepath,
                "prompt": prompt,
                "order": self.gallery_order_counter,
                "visible": True,
                "resolution": resolution,
                "aspect": aspect,
                "elapsed_sec": elapsed,
                "api_used": api_used,
                "generated_at": generated_at or datetime.now().isoformat(timespec="seconds"),
                "favorite": False,
                "generation_settings": generation_settings or {},
            }
            if filepath not in self.generated_paths:
                self.generated_paths.append(filepath)
            self.project_dirty = True
        # Previously we wrote a per-image .meta.json sidecar here as a
        # "crash-recovery backup". In practice nothing in the codebase ever
        # read those files back, and _maybe_autosave() now flushes the whole
        # project JSON every 15s during batches so the sidecar was pure
        # clutter that wouldn't even get cleaned up on image delete. Removed.

    def delete_gallery_item(self, filepath):
        if filepath in self.favorites:
            return False, "Unfavorite first", None
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
            # Also nuke any legacy .meta.json sidecar dropped by pre-v1723
            # builds. They were never read back; they just polluted the
            # output folder and got orphaned on delete.
            _side = filepath + ".meta.json"
            if os.path.exists(_side):
                try:
                    os.remove(_side)
                except Exception:
                    pass
            # Dict/set mutations must be serialized against HTTP threads
            # iterating over gallery_items (e.g. /api/gallery snapshot).
            with self.gallery_lock:
                if filepath in self.generated_paths:
                    self.generated_paths.remove(filepath)
                self.favorites.discard(filepath)
                self.gallery_items.pop(filepath, None)
            # Also remove from refs if present — remove_ref grabs ref_lock.
            removed_ref_idx = None
            with self.ref_lock:
                ref_count_before = len(self.ref_path_list)
                if filepath in self.ref_path_list:
                    removed_ref_idx = self.ref_path_list.index(filepath)
            if removed_ref_idx is not None:
                self.remove_ref(removed_ref_idx)
            self.project_dirty = True
            return True, "Deleted", {"removed_ref_idx": removed_ref_idx, "ref_count_before": ref_count_before}
        except Exception as e:
            return False, str(e)[:80], None

    def toggle_favorite(self, filepath):
        with self.gallery_lock:
            if filepath in self.favorites:
                self.favorites.discard(filepath)
                if filepath in self.gallery_items:
                    self.gallery_items[filepath]["favorite"] = False
                return False
            self.favorites.add(filepath)
            if filepath in self.gallery_items:
                self.gallery_items[filepath]["favorite"] = True
            return True

    def prune_missing_files(self):
        # Snapshot keys under the lock so we don't walk a dict that a worker
        # thread is simultaneously adding to. Disk I/O (os.path.exists) is
        # done OUTSIDE the lock because it's potentially slow on network
        # drives and would otherwise serialize all /api/gallery polls.
        with self.gallery_lock:
            keys = list(self.gallery_items.keys())
        missing = [p for p in keys if not os.path.exists(p)]
        if not missing:
            return 0
        # Second pass: remove them, again under the lock.
        refs_to_remove = []
        with self.gallery_lock:
            for fp in missing:
                self.favorites.discard(fp)
                if fp in self.generated_paths:
                    self.generated_paths.remove(fp)
                self.gallery_items.pop(fp, None)
        with self.ref_lock:
            for fp in missing:
                if fp in self.ref_path_list:
                    refs_to_remove.append(self.ref_path_list.index(fp))
        # remove_ref acquires ref_lock itself — call outside the snapshot lock
        # and iterate high→low so indices stay valid.
        for idx in sorted(refs_to_remove, reverse=True):
            self.remove_ref(idx)
        return len(missing)

    # --- Project ---
    def get_project_save_dir(self):
        # Ensure the directory exists so file dialogs actually land here
        try:
            os.makedirs(self.project_default_save_dir, exist_ok=True)
        except Exception:
            pass
        return self.project_default_save_dir

    def default_project_filename(self):
        return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_image_session.json"

    def collect_project_state(self):
        current_ref_paths = [p for p in self.get_effective_ref_paths() if p and os.path.exists(p)]
        with self.ref_lock:
            pinned_ref_paths = [
                p for i, p in enumerate(self.ref_path_list)
                if i < len(self.ref_pinned) and self.ref_pinned[i] and p and os.path.exists(p)
            ]
        # Snapshot under gallery_lock to avoid "dict changed size during iteration".
        # Secondary key (filepath) keeps legacy items (order=0 or missing) in a
        # deterministic order — prevents the gallery jumping around on reload.
        with self.gallery_lock:
            items = [
                self._serialize_item(fp, item)
                for fp, item in sorted(
                    self.gallery_items.items(),
                    key=lambda x: (x[1].get("order", 0), x[0]),
                )
            ]
        # Snapshot logs under its lock
        with self.log_lock:
            logs_str = "\n".join(self.logs)
        return {
            "project_version": 1,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "ui_state": {
                "prompt": self.compose_prompt(),
                "fixed_prompt": self.fixed_prompt,
                "prompt_sections": self.prompt_sections,
                "model": self.model,
                "aspect": self.aspect,
                "resolution": self.resolution,
                "count": str(self.count),
                "output_dir": self.output_dir,
                "naming": self.get_naming_settings(),
                "ref_paths": current_ref_paths,
                "pinned_ref_paths": pinned_ref_paths,
                "favorites_only": False,
                "search_query": "",
                "gallery_columns": self.gallery_columns,
            },
            "logs": logs_str,
            "gallery_items": items,
        }

    def _serialize_item(self, filepath, item):
        return {
            "filepath": filepath,
            "prompt": item.get("prompt", ""),
            "order": item.get("order", 0),
            "visible": True,
            "resolution": item.get("resolution", ""),
            "aspect": item.get("aspect", ""),
            "elapsed_sec": float(item.get("elapsed_sec", 0)),
            "api_used": item.get("api_used", ""),
            "generated_at": item.get("generated_at", ""),
            "favorite": filepath in self.favorites,
            "generation_settings": dict(item.get("generation_settings", {})),
        }

    def save_project(self, filepath):
        """Atomic save: write to .tmp then os.replace so a disk-full or
        crash mid-write cannot corrupt the target file."""
        data = self.collect_project_state()
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        tmp = filepath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, filepath)
        self.current_project_path = filepath
        self.project_dirty = False
        return True

    def load_project(self, filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            return False, str(e)[:120]

        if not isinstance(data, dict) or "ui_state" not in data:
            return False, "Invalid project file"

        ui = data.get("ui_state", {})
        self.model = ui.get("model", self.model)
        self.aspect = ui.get("aspect", self.aspect)
        self.resolution = ui.get("resolution", self.resolution)
        # Tolerant parse — older projects sometimes have count="" which would
        # raise ValueError and abort load mid-way, losing the whole session.
        try:
            self.count = max(1, min(10, int(str(ui.get("count", self.count)).strip() or self.count)))
        except (TypeError, ValueError):
            self.count = self.count or 1
        self.output_dir = ui.get("output_dir", self.output_dir)
        self.fixed_prompt = ui.get("fixed_prompt", "")
        self.prompt_sections = ui.get("prompt_sections", [ui.get("prompt", "")])
        if not self.prompt_sections:
            self.prompt_sections = [""]

        naming = ui.get("naming", {})
        self.naming_enabled = bool(naming.get("enabled"))
        self.naming_prefix = naming.get("prefix", "S010")
        self.naming_delimiter = naming.get("delimiter", "C010")
        self.naming_index_prefix = naming.get("index_prefix", "I")
        try:
            self.naming_padding = max(1, min(5, int(naming.get("padding", 3))))
        except (TypeError, ValueError):
            self.naming_padding = 3

        try:
            self.gallery_columns = max(1, min(8, int(ui.get("gallery_columns", 2))))
        except (TypeError, ValueError):
            self.gallery_columns = 2

        # Clear and restore refs
        self.clear_refs()
        ref_paths = [p for p in (ui.get("ref_paths") or []) if p]
        pinned = set(p for p in (ui.get("pinned_ref_paths") or []) if p)
        for rp in ref_paths:
            if os.path.exists(rp):
                self.add_ref_image(rp, pinned=rp in pinned)

        # Clear and restore gallery (under the lock so /api/gallery polls
        # don't see half-cleared state)
        with self.gallery_lock:
            self.gallery_items.clear()
            self.generated_paths.clear()
            self.favorites.clear()
            self.gallery_order_counter = 0
        # Reset run-scoped counters so the progress bar from a previous
        # session doesn't carry over visually
        self.done_count = 0
        self.fail_count = 0
        self.discarded_count = 0
        self.queue_count = 0

        saved_items = data.get("gallery_items", [])
        restored = 0
        missing = 0
        for si in sorted(saved_items, key=lambda x: x.get("order", 0)):
            fp = si.get("filepath")
            if not fp or not os.path.exists(fp):
                missing += 1
                continue
            self.gallery_order_counter = min(self.gallery_order_counter, si.get("order", 0)) - 1
            self.gallery_items[fp] = {
                "filepath": fp,
                "prompt": si.get("prompt", ""),
                "order": si.get("order", 0),
                "visible": True,
                "resolution": si.get("resolution", ""),
                "aspect": si.get("aspect", ""),
                "elapsed_sec": float(si.get("elapsed_sec", 0)),
                "api_used": si.get("api_used", ""),
                "generated_at": si.get("generated_at", ""),
                "favorite": bool(si.get("favorite")),
                "generation_settings": dict(si.get("generation_settings", {})),
            }
            if si.get("favorite"):
                self.favorites.add(fp)
            if fp not in self.generated_paths:
                self.generated_paths.append(fp)
            restored += 1

        # Restore logs
        saved_logs = data.get("logs", "")
        if saved_logs:
            with self.log_lock:
                self.logs = saved_logs.strip().split("\n")

        self.current_project_path = filepath
        self.project_dirty = False
        self.log(f"Loaded project: {restored} images, {missing} missing")
        return True, f"Loaded {restored} images"

    def get_recent_projects(self, limit=6):
        # Scan both the current save dir and the legacy Desktop location
        search_dirs = [
            self.get_project_save_dir(),
            os.path.join(os.path.expanduser("~/Desktop"), "NanoBanana_Output", "NanoBanana JSON"),
        ]
        candidates = []
        seen = set()
        for pdir in search_dirs:
            if not pdir or not os.path.isdir(pdir):
                continue
            try:
                for n in os.listdir(pdir):
                    if not n.lower().endswith(".json"):
                        continue
                    fp = os.path.join(pdir, n)
                    if fp in seen:
                        continue
                    seen.add(fp)
                    candidates.append(fp)
            except Exception:
                continue
        if not candidates:
            return []
        entries = []
        candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for fp in candidates[:limit]:
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            ui = data.get("ui_state", {})
            items = data.get("gallery_items", [])
            preview = ""
            for it in sorted(items, key=lambda x: x.get("order", 0)):
                if it.get("filepath") and os.path.exists(it["filepath"]):
                    preview = it["filepath"]
                    break
            entries.append({
                "filepath": fp,
                "name": os.path.basename(fp),
                "modified_at": os.path.getmtime(fp),
                "prompt": ui.get("prompt", ""),
                "image_count": len(items),
                "preview_path": preview,
            })
        return entries

    # --- Generation ---
    def get_default_thinking_config(self, model):
        if model != "gemini-3.1-flash-image-preview":
            return None
        try:
            return types.ThinkingConfig(thinking_level="high")
        except Exception:
            return None

    def sleep_with_cancel(self, seconds, step=0.1):
        remaining = max(0.0, float(seconds))
        while remaining > 0:
            if self.cancel_flag:
                return False
            chunk = min(step, remaining)
            time.sleep(chunk)
            remaining -= chunk
        return not self.cancel_flag

    def generate_one_image(self, job, prompt, ref_payloads, model, img_cfg, modalities):
        idx = job["index"]
        total = job["total"]
        seed = job["seed"]
        preferred = job["preferred_provider"]

        self.log(f"[{idx+1}/{total}] Queued on {self.get_provider_label(preferred)} (seed {seed})")

        contents = [types.Content(role="user", parts=self.build_user_parts(prompt, ref_payloads))]
        cfg_kw = dict(
            temperature=1.0,
            seed=seed,
            response_modalities=modalities,
            image_config=types.ImageConfig(**img_cfg),
        )
        tc = self.get_default_thinking_config(model)
        if tc:
            cfg_kw["thinking_config"] = tc
        config = types.GenerateContentConfig(**cfg_kw)

        start = time.time()
        max_retries = 5
        delay = 10

        for attempt in range(max_retries):
            if self.cancel_flag:
                return {"status": "cancelled", "index": idx, "seed": seed}
            try:
                resp, api_used = self.call_api(model, contents, config, preferred_provider=preferred)
                elapsed = time.time() - start
                pil = self.extract_image_from_response(resp)
                if pil is not None:
                    return {"status": "success", "index": idx, "seed": seed,
                            "image": pil, "elapsed": elapsed, "api_used": api_used}

                # Log WHY the primary provider returned no image
                self.diagnose_empty_response(resp, self.get_provider_label(api_used))

                # Try fallback provider if no image
                fallback_providers = [
                    p for p in self.build_provider_order()
                    if p != api_used and self.get_provider_client(p)
                ]
                for fp in fallback_providers:
                    fl = self.get_provider_label(fp)
                    self.log(f"[{idx+1}] No image -> {fl} fallback")
                    try:
                        resp2, fu = self.call_api(model, contents, config, preferred_provider=fp)
                    except Exception:
                        continue
                    pil2 = self.extract_image_from_response(resp2)
                    if pil2:
                        return {"status": "success", "index": idx, "seed": seed,
                                "image": pil2, "elapsed": time.time()-start, "api_used": fu}
                    # Log why the fallback also failed
                    self.diagnose_empty_response(resp2, fl)

                self.log(f"[{idx+1}] No image (attempt {attempt+1})")
                if attempt < max_retries - 1:
                    if not self.sleep_with_cancel(3):
                        return {"status": "cancelled", "index": idx, "seed": seed}
                    continue
                return {"status": "failed", "index": idx, "seed": seed,
                        "error": "No image in response", "elapsed": elapsed}

            except Exception as e:
                err = str(e)
                if err == "Cancelled":
                    return {"status": "cancelled", "index": idx, "seed": seed}
                elapsed = time.time() - start
                if self.is_retryable_error(err) and attempt < max_retries - 1:
                    wt = delay + random.uniform(2, 8)
                    self.log(f"[{idx+1}] Retryable error. Wait {wt:.0f}s (retry {attempt+1}/{max_retries})")
                    if not self.sleep_with_cancel(wt):
                        return {"status": "cancelled", "index": idx, "seed": seed}
                    delay = min(delay * 2, 120)
                    continue
                return {"status": "failed", "index": idx, "seed": seed,
                        "error": err[:120], "elapsed": elapsed}

        return {"status": "cancelled", "index": idx, "seed": seed}

    def _maybe_autosave(self, min_interval=15.0):
        """Best-effort project save during long batches. Throttled to avoid
        disk thrash when many images complete back-to-back. Silent on error —
        the sidecar .meta.json files already cover per-image metadata."""
        now = time.time()
        if now - self._last_autosave_ts < min_interval:
            return
        if not self.project_dirty:
            return
        try:
            save_dir = self.get_project_save_dir()
            os.makedirs(save_dir, exist_ok=True)
            fp = self.current_project_path or os.path.join(save_dir, self.default_project_filename())
            self.save_project(fp)
            self._last_autosave_ts = now
        except Exception:
            # Don't let autosave failures break the generation loop
            self._last_autosave_ts = now

    def _pop_pending_job(self):
        with self.pending_jobs_lock:
            if not self.pending_jobs:
                return None
            job = self.pending_jobs.pop(0)
            self.active_job_count += 1
            return job

    def _finish_pending_job(self):
        with self.pending_jobs_lock:
            self.active_job_count = max(0, self.active_job_count - 1)

    def get_queue_outstanding(self):
        with self.pending_jobs_lock:
            return len(self.pending_jobs) + self.active_job_count

    def get_queue_pending(self):
        with self.pending_jobs_lock:
            return len(self.pending_jobs)

    def gen_worker(self):
        try:
            self._gen_worker_body()
        except Exception as e:
            self.log(f"Worker crashed: {str(e)[:120]}")
        finally:
            # Three-way teardown:
            #   - cancel_flag set  -> user Stopped; drop pending (including any
            #                          that raced in while we were shutting down)
            #   - pending non-empty -> /api/generate queued jobs in the window
            #                          between our body exiting and this finally
            #                          taking the lock. Respawn a worker to drain
            #                          them; keep is_generating=True so follow-up
            #                          requests still see a live batch.
            #   - otherwise         -> clean exit.
            #
            # v2101 unconditionally cleared pending_jobs here, which on a normal
            # end-of-batch exit would delete race-added jobs and leave the user
            # with "outstanding N, no worker" — the exact stall bug we set out
            # to kill. v2102 splits the three cases.
            respawn = False
            with self.pending_jobs_lock:
                if self.cancel_flag:
                    self.pending_jobs.clear()
                    self.is_generating = False
                    self.cancel_flag = False
                    self.active_job_count = 0
                elif self.pending_jobs:
                    self.active_job_count = 0
                    respawn = True
                else:
                    self.is_generating = False
                    self.cancel_flag = False
                    self.active_job_count = 0
            if respawn:
                threading.Thread(target=self.gen_worker, daemon=True).start()
            else:
                self.push_event({"type": "done", "done": self.done_count, "failed": self.fail_count})

    def _gen_worker_body(self):
        max_workers = self.max_parallel_requests
        active = {}

        # Do NOT use the `with` block — its __exit__ calls shutdown(wait=True)
        # which would block Stop by up to the SDK request timeout (~60s) while
        # the last in-flight generate_content calls finish. Instead, shut down
        # explicitly with cancel_futures=True so Stop feels instant.
        executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="nano-gen")
        try:
            while True:
                # Fill workers from queue
                while not self.cancel_flag and len(active) < max_workers:
                    job = self._pop_pending_job()
                    if job is None:
                        break
                    try:
                        fut = executor.submit(
                            self.generate_one_image,
                            {"index": job["index"],
                             # queue_count is the authoritative batch total
                             # (updated under pending_jobs_lock whenever new
                             # jobs are extended). Pre-v2101 this was a
                             # hardcoded 0 placeholder, causing [1/0], [2/0]
                             # log lines that looked like div-by-zero.
                             "total": self.queue_count,
                             "seed": job["seed"],
                             "preferred_provider": job["preferred_provider"]},
                            job["prompt"], job["ref_payloads"], job["model"],
                            job["img_cfg"], ["IMAGE"]
                        )
                    except Exception as e:
                        self.log(f"Submit failed: {str(e)[:120]}")
                        self._finish_pending_job()
                        self.fail_count += 1
                        continue
                    active[fut] = job

                if not active:
                    break

                done_set, _ = wait(list(active.keys()), timeout=0.1, return_when=FIRST_COMPLETED)
                if not done_set:
                    continue

                for fut in done_set:
                    job = active.pop(fut)
                    try:
                        result = fut.result()
                    except Exception as e:
                        result = {"status": "failed", "index": job["index"],
                                  "seed": job["seed"], "error": str(e)[:120], "elapsed": 0}

                    self._finish_pending_job()
                    idx = result["index"]
                    prompt = job["prompt"]
                    model = job["model"]
                    aspect = job["aspect"]
                    resolution = job["resolution"]
                    naming = job["naming"]

                    if result["status"] == "success":
                        pil = result["image"]
                        elapsed = result["elapsed"]
                        api_used = result["api_used"]
                        seed = result["seed"]
                        fn = self.make_filename(seed, naming)
                        fp = os.path.join(job["output_dir"], fn)
                        self.save_generated_image(pil, fp, prompt, model)
                        self.done_count += 1

                        gen_at = datetime.now().isoformat(timespec="seconds")
                        # Save the actual batch size (from /api/generate) so
                        # Load restores the count the user picked for that run,
                        # not a hardcoded 1.
                        saved_count = int(job.get("batch_count") or 1)
                        if saved_count < 1:
                            saved_count = 1
                        if saved_count > 10:
                            saved_count = 10
                        # Read every prompt-related value from the JOB
                        # snapshot, NOT from live state. Pre-v2103 this read
                        # self.fixed_prompt / self.prompt_sections, which
                        # meant any prompt edit between submit and completion
                        # (e.g. user starts typing a new prompt while the
                        # batch is still rendering, or clears the prompt to
                        # set up the next idea) would overwrite the saved
                        # setup for images that had already been generated
                        # with the ORIGINAL prompt. Clicking Load on those
                        # images then restored the edited/blank prompt —
                        # the "Load doesn't bring the prompt back" bug.
                        # refs/model/aspect/etc were already being read from
                        # the job snapshot; fixed_prompt and prompt_sections
                        # were the only two values still leaking live state.
                        gen_settings = {
                            "prompt": prompt,
                            "fixed_prompt": job.get("fixed_prompt", ""),
                            "prompt_sections": list(job.get("prompt_sections") or []),
                            "model": model, "aspect": aspect, "resolution": resolution,
                            "count": saved_count, "output_dir": job["output_dir"],
                            "naming": naming,
                            "ref_paths": list(job.get("ref_paths", [])),
                            "pinned_ref_paths": list(job.get("pinned_ref_paths", [])),
                        }
                        self.add_gallery_item(fp, prompt, elapsed, api_used,
                                              aspect=aspect, resolution=resolution,
                                              generated_at=gen_at,
                                              generation_settings=gen_settings)
                        self.push_event({
                            "type": "image_done",
                            "filepath": fp,
                            "filename": fn,
                            "elapsed": round(elapsed, 1),
                            "api_used": api_used,
                            "done": self.done_count,
                            "failed": self.fail_count,
                            "total": self.queue_count,
                            "outstanding": self.get_queue_outstanding(),
                        })
                        self.log(f"[{idx+1}] Saved {fn} ({elapsed:.1f}s via {api_used})")
                    elif result["status"] == "failed":
                        self.fail_count += 1
                        self.push_event({
                            "type": "image_failed",
                            "index": idx,
                            "error": result.get("error", ""),
                            "done": self.done_count,
                            "failed": self.fail_count,
                            "total": self.queue_count,
                            "outstanding": self.get_queue_outstanding(),
                        })
                        self.log(f"[{idx+1}] {result.get('error','')} ({result.get('elapsed',0):.1f}s)")
                    else:
                        self.log(f"[{idx+1}] Cancelled")

                # Refill loop
                if self.cancel_flag and not active:
                    break

                # Incremental auto-save — flush the project JSON every so often
                # so a crash mid-batch doesn't lose prompts/settings/gallery.
                # Per-image .meta.json sidecars already survive on their own.
                self._maybe_autosave()
        finally:
            # Shut down without waiting on in-flight requests. Cancel queued but
            # not-yet-started futures so Stop doesn't hang the UI for ~60s
            # while the SDK finishes a slow generate_content call.
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                # Older Python without cancel_futures — fall back to wait=False.
                executor.shutdown(wait=False)

        self.log(f"Finished: {self.done_count} saved, {self.fail_count} failed")

        # Auto-save project
        try:
            save_dir = self.get_project_save_dir()
            os.makedirs(save_dir, exist_ok=True)
            if self.current_project_path:
                self.save_project(self.current_project_path)
            else:
                fp = os.path.join(save_dir, self.default_project_filename())
                self.save_project(fp)
        except Exception:
            pass


# ==========================================
# Flask App
# ==========================================
if getattr(sys, 'frozen', False):
    _flask_base = sys._MEIPASS
else:
    _flask_base = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(_flask_base, 'templates'),
    static_folder=os.path.join(_flask_base, 'static'),
)
# Cap upload size at 40 MB so a rogue drop can't OOM the process
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024
state = AppState()


# --- CSRF protection ---
# Any local webpage on 127.0.0.1 could POST to our endpoints. Require a
# custom header whose value we print into the HTML template; local pages
# in a browser won't see that value and will be rejected.
import secrets as _secrets
CSRF_TOKEN = _secrets.token_urlsafe(32)

# Endpoints exempt from CSRF (GET is safe; our /api/status polling is GET-only)
_CSRF_EXEMPT_METHODS = {"GET", "HEAD", "OPTIONS"}
_CSRF_EXEMPT_PATHS = {"/api/version"}  # any public-ish read-only endpoints


@app.before_request
def _csrf_guard():
    # Skip if it's our own UI (served from same origin with token)
    if request.method in _CSRF_EXEMPT_METHODS:
        return None
    if request.path in _CSRF_EXEMPT_PATHS:
        return None
    # Non-API routes (HTML views) pass through
    if not request.path.startswith("/api/"):
        return None
    tok = request.headers.get("X-NB-Token", "")
    if tok != CSRF_TOKEN:
        return ("forbidden", 403)
    return None


@app.after_request
def add_no_cache(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def _read_version():
    """VERSION lives inside the PyInstaller bundle (or next to the source
    file in dev mode). No overlay paths — the updater swaps the whole EXE."""
    candidates = [
        os.path.join(getattr(sys, '_MEIPASS', ''), "VERSION"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION"),
        os.path.join(os.getcwd(), "VERSION"),
    ]
    for vf in candidates:
        try:
            with open(vf, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            continue
    return "unknown"


# Unique cache-buster per server start — prevents pywebview's embedded
# WebView from serving stale CSS/JS after a rebuild.
_BUILD_ID = str(int(time.time()))


# --- Release-notes-on-first-launch-after-update ---
def _user_data_dir():
    d = os.path.join(os.path.expanduser("~"), ".nanobanana")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _last_seen_version_file():
    return os.path.join(_user_data_dir(), "last_seen_version.txt")


def _fetch_release_notes(version_tag):
    """Fetch release body from GitHub for the given tag. Returns "" on any error."""
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://api.github.com/repos/productionkhu-tech/freewill-nanobanana/releases/tags/{version_tag}",
            headers={
                "User-Agent": "NanoBanana",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return (data.get("body") or "").strip()
    except Exception as e:
        print(f"  release notes fetch failed: {e}")
        return ""


@app.route("/api/release-notes-check")
def release_notes_check():
    """Called once on app startup. Shows the popup whenever we can tell the
    user just got a newer version than what they previously saw.

    Detection strategy (in priority order):
      1. last_seen file exists with a DIFFERENT version → show
      2. last_seen missing BUT user_updates/ overlay exists (= user just
         updated from an older EXE that had no release-notes endpoint) → show
      3. last_seen missing AND no overlay → brand-new install, don't show
    """
    current = _read_version()
    vfile = _last_seen_version_file()
    try:
        with open(vfile, "r", encoding="utf-8") as f:
            last = f.read().strip()
    except Exception:
        last = ""

    # Show when the bundled VERSION is different from what the user last
    # saw on this machine. Because the updater now swaps the whole EXE,
    # the new bundled VERSION is guaranteed to match the remote after an
    # update — so this comparison is reliable.
    show = bool(last) and last != current
    notes = ""
    if show:
        notes = _fetch_release_notes(current)
        if not notes:
            notes = "새로운 버전이 적용되었습니다."

    # Record current as seen so popup doesn't show again
    try:
        with open(vfile, "w", encoding="utf-8") as f:
            f.write(current)
    except Exception:
        pass

    return jsonify({
        "show": show,
        "version": current,
        "previous": last,
        "notes": notes,
    })


@app.after_request
def _no_cache_static(resp):
    """Force fresh CSS/JS on every request so pywebview's WebView2 cache
    never serves stale stylesheets after a rebuild."""
    ct = resp.headers.get("Content-Type", "")
    if any(t in ct for t in ("text/css", "application/javascript", "text/html")):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


def _render_html(template_name):
    html = render_template(template_name)
    html = html.replace("__VERSION__", _read_version() + "." + _BUILD_ID)
    html = html.replace("__CSRF_TOKEN__", CSRF_TOKEN)
    resp = Response(html)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.route("/")
def index():
    return _render_html("index.html")


@app.route("/viewer")
def viewer():
    return _render_html("viewer.html")


@app.route("/prompt-popup")
def prompt_popup():
    return _render_html("prompt_popup.html")


@app.route("/api/version")
def api_version():
    return jsonify({"version": _read_version()})


@app.route("/api/check-update", methods=["POST"])
def api_check_update():
    """Manual "Check for updates" trigger from the UI. Runs the same check
    the background thread runs at startup and returns the outcome so the
    frontend can show a toast even if the modal popup flow is blocked
    (network hiccup, user dismissed too fast, etc.)."""
    try:
        from updater import check_for_update
        has_update, current, remote = check_for_update()
        if not remote:
            msg = f"Update check failed (local {current}) - network blocked?"
            status = "error"
        elif has_update:
            msg = f"Update available: {current} -> {remote}"
            status = "available"
        else:
            msg = f"Already on latest version ({current})"
            status = "current"
        state.log(f"Manual {msg}")
        return jsonify({
            "ok": True, "status": status, "message": msg,
            "current": current, "remote": remote, "has_update": bool(has_update),
        })
    except Exception as e:
        state.log(f"Manual update check error: {str(e)[:80]}")
        return jsonify({"ok": False, "error": str(e)[:120]})


# Guard so the user clicking "Update" twice doesn't spawn two downloaders.
_apply_update_lock = threading.Lock()
_apply_update_running = [False]

@app.route("/api/apply-update", methods=["POST"])
def api_apply_update():
    """Kicks off the download + --updater spawn flow on a background thread.

    This is the frontend-driven replacement for the old Python-side
    MessageBox prompt. Sequence from the UI:
      1. frontend shows in-page "Update available" dialog
      2. user clicks Yes -> frontend POSTs here
      3. we start a bg thread that:
           - downloads NanoBanana.new.exe (pushes update_progress events)
           - spawns NanoBanana.new.exe --updater <our_path>
           - os._exit(0)
      4. frontend overlay watches update_progress + update_swap events
      5. app disappears, new app launches, release-notes popup shows
    """
    with _apply_update_lock:
        if _apply_update_running[0]:
            return jsonify({"ok": False, "error": "Update already in progress"})
        _apply_update_running[0] = True

    def _worker():
        try:
            from updater import check_for_update, apply_update_and_relaunch
            has_update, current, remote = check_for_update()
            if not has_update:
                state.push_event({"type": "update_swap", "phase": "noop",
                                  "message": f"이미 최신 버전입니다 ({current})"})
                _apply_update_running[0] = False
                return

            def _on_dl_progress(done, total):
                pct = int(done * 100 / total) if total else 0
                state.push_event({
                    "type": "update_progress",
                    "done": done, "total": total, "pct": pct,
                })

            state.push_event({"type": "update_swap", "phase": "downloading",
                              "message": f"{remote} 다운로드 중..."})
            apply_update_and_relaunch(remote, on_progress=_on_dl_progress)
            state.push_event({"type": "update_swap", "phase": "handing_off",
                              "message": "설치 준비 중..."})
            # One poll tick (800ms) for the frontend to pick up the last
            # event before our process dies.
            time.sleep(1.0)
            # os._exit releases our EXE file handle so the --updater
            # child can atomically replace it.
            os._exit(0)
        except Exception as e:
            state.log(f"apply-update failed: {str(e)[:120]}")
            state.push_event({"type": "update_swap", "phase": "failed",
                              "message": f"업데이트 실패: {str(e)[:120]}"})
            _apply_update_running[0] = False

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/status")
def api_status():
    outstanding = state.get_queue_outstanding()
    # Piggyback the close-requested flag here so JS doesn't need a separate poll
    close_req = state.close_requested
    state.close_requested = False
    return jsonify({
        "vertex": state.vertex_status,
        "studio": state.studio_status,
        "is_generating": state.is_generating,
        "done": state.done_count,
        "failed": state.fail_count,
        "total": state.queue_count,
        "outstanding": outstanding,
        "max_queue": state.max_queued_images,
        "project_dirty": state.project_dirty,
        "current_project": state.current_project_path or "",
        "close_requested": close_req,
    })


@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify({
        "model": state.model,
        "aspect": state.aspect,
        "resolution": state.resolution,
        "count": state.count,
        "output_dir": state.output_dir,
        "fixed_prompt": state.fixed_prompt,
        "prompt_sections": state.prompt_sections,
        "naming_enabled": state.naming_enabled,
        "naming_prefix": state.naming_prefix,
        "naming_delimiter": state.naming_delimiter,
        "naming_index_prefix": state.naming_index_prefix,
        "naming_padding": state.naming_padding,
        "gallery_columns": state.gallery_columns,
        "ref_limit": state.get_ref_limit(),
    })


def _safe_int(value, default, lo=None, hi=None):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if lo is not None: n = max(lo, n)
    if hi is not None: n = min(hi, n)
    return n


@app.route("/api/settings", methods=["POST"])
def update_settings():
    d = request.json or {}
    for k in ("model", "aspect", "resolution", "fixed_prompt",
              "naming_prefix", "naming_delimiter", "naming_index_prefix"):
        if k in d and d[k] is not None:
            setattr(state, k, str(d[k]))
    if "count" in d:
        # Clamp to the valid UI range so a rogue client can't brick the dropdown
        state.count = _safe_int(d.get("count"), state.count, lo=1, hi=10)
    if "output_dir" in d and d["output_dir"]:
        state.output_dir = str(d["output_dir"])
    if "naming_enabled" in d:
        state.naming_enabled = bool(d.get("naming_enabled"))
    if "naming_padding" in d:
        state.naming_padding = _safe_int(d.get("naming_padding"), state.naming_padding, lo=1, hi=5)
    if "prompt_sections" in d:
        ps = d.get("prompt_sections")
        if isinstance(ps, list):
            state.prompt_sections = [str(x) for x in ps]
    if "gallery_columns" in d:
        state.gallery_columns = _safe_int(d.get("gallery_columns"), state.gallery_columns, lo=1, hi=8)
    state.project_dirty = True
    return jsonify({"ok": True, "ref_limit": state.get_ref_limit()})


@app.route("/api/logs")
def get_logs():
    with state.log_lock:
        return jsonify({"logs": list(state.logs)})


@app.route("/api/events")
def get_events():
    return jsonify({"events": state.pop_events()})


# --- References ---
@app.route("/api/refs", methods=["GET"])
def get_refs():
    refs = []
    for i, fp in enumerate(state.ref_path_list):
        pinned = i < len(state.ref_pinned) and state.ref_pinned[i]
        refs.append({
            "index": i,
            "path": fp,
            "filename": os.path.basename(fp),
            "pinned": pinned,
            "exists": os.path.exists(fp),
        })
    return jsonify({
        "refs": refs,
        "limit": state.get_ref_limit(),
        "count": len(state.ref_images),
    })


@app.route("/api/refs/upload", methods=["POST"])
def upload_refs():
    import hashlib
    files = request.files.getlist("files")
    added = 0
    os.makedirs(state.temp_ref_dir, exist_ok=True)
    for f in files:
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            continue

        # Read bytes once, hash them to dedupe identical content
        data = f.read()
        if not data:
            continue
        digest = hashlib.sha1(data).hexdigest()[:16]
        fp = os.path.join(state.temp_ref_dir, f"ref_{digest}{ext}")

        # If a file with this exact content already exists on disk, reuse it
        if not os.path.exists(fp):
            with open(fp, "wb") as out:
                out.write(data)

        # Track so we know it was cached by this app (used for accounting, not deletion)
        state.temp_ref_paths.add(fp)

        # add_ref_image already skips if the same path is already in ref_path_list
        if state.add_ref_image(fp):
            added += 1
    return jsonify({"ok": True, "added": added})


@app.route("/api/refs/download-url", methods=["POST"])
def download_ref_url():
    """Download a remote image URL and add as reference.

    Why this exists: when the user drags an <img> from another browser
    window / webpage, Chromium often leaves DataTransfer.files empty and
    only populates text/uri-list or text/html with the image URL. Fetch()
    from the renderer is blocked by CORS for most image hosts, so we
    can't get the bytes client-side. The Flask server has no CORS
    constraint, so it does the download and saves the file just like
    /api/refs/upload would for a dragged local file.

    Body: {url: "https://..."}
    """
    import hashlib
    import urllib.parse
    import urllib.request
    import urllib.error

    d = request.json or {}
    url = (d.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "No URL"})

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return jsonify({"ok": False, "error": "Only http/https URLs supported"})

    MAX_BYTES = 40 * 1024 * 1024
    TIMEOUT = 30

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "NanoBanana-RefDownloader/1.0",
            "Accept": "image/*,*/*;q=0.8",
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower().split(";")[0].strip()
            if not content_type.startswith("image/"):
                return jsonify({
                    "ok": False,
                    "error": f"Not an image (type: {content_type or 'unknown'})",
                })
            content_length = resp.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > MAX_BYTES:
                        return jsonify({"ok": False, "error": "Image too large (>40MB)"})
                except ValueError:
                    pass
            chunks = []
            total = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES:
                    return jsonify({"ok": False, "error": "Image too large (>40MB)"})
                chunks.append(chunk)
            data = b"".join(chunks)
    except urllib.error.HTTPError as e:
        return jsonify({"ok": False, "error": f"HTTP {e.code}"})
    except urllib.error.URLError as e:
        return jsonify({"ok": False, "error": f"Network error: {str(e.reason)[:80]}"})
    except Exception as e:
        return jsonify({"ok": False, "error": f"Download failed: {str(e)[:80]}"})

    if not data:
        return jsonify({"ok": False, "error": "Empty response"})

    # Pick extension. Prefer content-type; fall back to URL path suffix.
    ext_from_type = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "image/x-ms-bmp": ".bmp",
    }.get(content_type)
    if ext_from_type is None:
        url_ext = os.path.splitext(parsed.path)[1].lower()
        if url_ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            ext_from_type = url_ext
    if ext_from_type not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        return jsonify({"ok": False, "error": f"Unsupported format ({content_type})"})

    # Verify the bytes really are an image PIL can read.
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
    except Exception:
        return jsonify({"ok": False, "error": "File is not a valid image"})

    os.makedirs(state.temp_ref_dir, exist_ok=True)
    digest = hashlib.sha1(data).hexdigest()[:16]
    fp = os.path.join(state.temp_ref_dir, f"ref_{digest}{ext_from_type}")
    if not os.path.exists(fp):
        with open(fp, "wb") as out:
            out.write(data)
    state.temp_ref_paths.add(fp)

    # Report limit / duplicate specifically so the client can show a useful toast.
    with state.ref_lock:
        if fp in state.ref_path_list:
            return jsonify({"ok": False, "error": "Already a reference"})
        limit = state.get_ref_limit()
        if len(state.ref_images) >= limit:
            return jsonify({
                "ok": False,
                "error": f"Max {limit} reference images (drop on a slot to replace)",
                "limit_reached": True,
            })
    if state.add_ref_image(fp):
        return jsonify({"ok": True, "added": 1})
    return jsonify({"ok": False, "error": "Could not add"})


@app.route("/api/browse-replace-ref", methods=["POST"])
def browse_replace_ref():
    d = request.json or {}
    idx = d.get("index", -1)
    with state.ref_lock:
        in_range = 0 <= idx < len(state.ref_images)
    if not in_range:
        return jsonify({"ok": False, "error": "Invalid index"})
    try:
        from tkinter import filedialog
        root = _make_dialog_root()
        initial = state.output_dir if os.path.isdir(state.output_dir) else os.path.expanduser("~")
        fp = filedialog.askopenfilename(
            parent=root,
            title=f"Replace Reference Image {idx + 1}",
            filetypes=[("Image Files", "*.png;*.jpg;*.jpeg;*.webp;*.bmp"), ("All Files", "*.*")],
            initialdir=initial,
        )
        root.destroy()
        if not fp:
            return jsonify({"ok": False})
        # Delegate to the locked state method — avoids a second unlocked
        # code path that used to race with /api/refs concurrent reads.
        ok = state.replace_ref(idx, fp)
        if ok:
            state.log(f"Replaced ref {idx + 1}: {os.path.basename(fp)}")
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Replace failed"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:80]})


@app.route("/api/refs/add-path", methods=["POST"])
def add_ref_path():
    d = request.json or {}
    fp = d.get("filepath", "")
    if not fp or not os.path.exists(fp):
        return jsonify({"ok": False, "error": "File not found"})
    ok = state.add_ref_image(fp)
    return jsonify({"ok": ok})


@app.route("/api/refs/<int:idx>", methods=["DELETE"])
def remove_ref(idx):
    ok = state.remove_ref(idx)
    return jsonify({"ok": ok})


@app.route("/api/refs/clear", methods=["POST"])
def clear_refs():
    d = request.json or {}
    state.clear_refs(preserve_pinned=d.get("preserve_pinned", False))
    return jsonify({"ok": True})


@app.route("/api/refs/pin/<int:idx>", methods=["POST"])
def pin_ref(idx):
    state.toggle_ref_pin(idx)
    with state.ref_lock:
        pinned = idx < len(state.ref_pinned) and state.ref_pinned[idx]
    return jsonify({"ok": True, "pinned": pinned})


@app.route("/api/refs/replace/<int:idx>", methods=["POST"])
def replace_ref_upload(idx):
    """Replace ref at idx with an uploaded file. Preserves position/pin."""
    import hashlib
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "No file"})
    ext = os.path.splitext(f.filename or "")[1].lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        return jsonify({"ok": False, "error": "Unsupported format"})
    # Size cap (enforced separately from MAX_CONTENT_LENGTH so error message is clean)
    data = f.read()
    if not data:
        return jsonify({"ok": False, "error": "Empty file"})
    if len(data) > 40 * 1024 * 1024:
        return jsonify({"ok": False, "error": "File too large (>40MB)"})
    digest = hashlib.sha1(data).hexdigest()[:16]
    os.makedirs(state.temp_ref_dir, exist_ok=True)
    target = os.path.join(state.temp_ref_dir, f"ref_{digest}{ext}")
    if not os.path.exists(target):
        with open(target, "wb") as out:
            out.write(data)
    state.temp_ref_paths.add(target)
    ok = state.replace_ref(idx, target)
    return jsonify({"ok": ok})


@app.route("/api/refs/paste", methods=["POST"])
def paste_ref():
    ok, msg = state.paste_clipboard_ref()
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/refs/thumb/<int:idx>")
def ref_thumb(idx):
    # Snapshot the PIL image under the ref lock so a concurrent remove/
    # replace can't shrink the list between the bounds check and the index
    # access, and can't close the image object out from under us.
    with state.ref_lock:
        if idx < 0 or idx >= len(state.ref_images):
            return "", 404
        pil = state.ref_images[idx].copy()
    pil.thumbnail((100, 100), Image.LANCZOS)
    buf = io.BytesIO()
    # PNG preserves alpha — PNG logos/icons stay as transparent cutouts
    # against the dark ref-cell background instead of gaining a white halo.
    pil.save(buf, "PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


# --- Gallery ---
@app.route("/api/gallery")
def get_gallery():
    state.prune_missing_files()
    items = []
    for fp, item in sorted(state.gallery_items.items(), key=lambda x: (x[1].get("order", 0), x[0])):
        items.append({
            "filepath": fp,
            "filename": os.path.basename(fp),
            "prompt": item.get("prompt", ""),
            "order": item.get("order", 0),
            "resolution": item.get("resolution", ""),
            "aspect": item.get("aspect", ""),
            "elapsed_sec": round(item.get("elapsed_sec", 0), 1),
            "api_used": item.get("api_used", ""),
            "generated_at": item.get("generated_at", ""),
            "favorite": fp in state.favorites,
        })
    return jsonify({"items": items, "count": len(items)})


def _is_path_allowed(fp):
    """Only allow files that the app itself produced or the user explicitly
    pulled into the app. Prevents /api/gallery/image?path=C:\\Windows\\win.ini
    exfiltration from a malicious local webpage hitting 127.0.0.1.

    Allowlist (resolved real paths):
      - anything under state.output_dir
      - anything under state.temp_ref_dir (clipboard / uploaded refs)
      - project save dir (for thumbnails of recent-project previews)
      - legacy Desktop project location
      - explicit gallery/ref paths (prefix check handles most; this is the
        escape hatch for cases where the user moved their output dir after
        generating)

    Directory check is done FIRST because it's the cheap case and handles the
    99% path. The exact-match fallback was previously resolving realpath for
    every gallery+ref item on every request — O(n) per /api/gallery/image and
    /api/gallery/thumb, which destroys perf once the gallery grows past a few
    hundred images.
    """
    if not fp:
        return False
    try:
        real = os.path.realpath(fp)
    except Exception:
        return False
    if not os.path.isfile(real):
        return False

    # Check allowed parent directories first — cheap, covers the common case.
    allowed_dirs = []
    for getter in (
        lambda: state.output_dir,
        lambda: state.temp_ref_dir,
        lambda: state.get_project_save_dir(),
        lambda: os.path.join(os.path.expanduser("~/Desktop"), "NanoBanana_Output", "NanoBanana JSON"),
    ):
        try:
            d = os.path.realpath(getter())
            if d:
                allowed_dirs.append(d)
        except Exception:
            continue
    for d in allowed_dirs:
        try:
            if os.path.commonpath([real, d]) == d:
                return True
        except ValueError:
            # Different drive letters (C: vs D:) — commonpath raises, just skip.
            continue

    # Exact-match fallback for paths outside the allowed dirs (user-picked
    # refs, moved output folder). Snapshot under locks, then do the cheap
    # string compare WITHOUT calling realpath on every item (the original
    # code did n realpaths per request — perf cliff at ~500 items).
    with state.gallery_lock:
        gallery_paths = list(state.gallery_items.keys())
    with state.ref_lock:
        ref_paths = list(state.ref_path_list)
    # Normcase for case-insensitive match on Windows; no realpath needed
    # because the incoming `real` already resolved symlinks.
    real_lower = os.path.normcase(real)
    for p in gallery_paths:
        if os.path.normcase(p) == real_lower:
            return True
    for p in ref_paths:
        if os.path.normcase(p) == real_lower:
            return True
    return False


@app.route("/api/gallery/image")
def serve_gallery_image():
    fp = request.args.get("path", "")
    if not fp or not _is_path_allowed(fp):
        return "", 404
    return send_file(fp, mimetype="image/png")


@app.route("/api/gallery/thumb")
def serve_gallery_thumb():
    fp = request.args.get("path", "")
    try:
        size = int(request.args.get("size", 360))
    except (TypeError, ValueError):
        size = 360
    size = max(32, min(size, 2048))
    if not fp or not _is_path_allowed(fp):
        return "", 404
    try:
        with Image.open(fp) as img:
            pil = _to_rgb_flatten(img)
            pil.thumbnail((size, size), Image.LANCZOS)
            buf = io.BytesIO()
            pil.save(buf, "JPEG", quality=85)
        buf.seek(0)
        return send_file(buf, mimetype="image/jpeg")
    except Exception:
        return "", 500


@app.route("/api/gallery/delete", methods=["POST"])
def delete_gallery():
    d = request.json or {}
    paths = d.get("paths", [])
    deleted = 0
    errors = []
    ref_removals = []
    for fp in paths:
        ok, msg, meta = state.delete_gallery_item(fp)
        if ok:
            deleted += 1
            if meta and meta.get("removed_ref_idx") is not None:
                ref_removals.append(meta)
        else:
            errors.append(msg)
    return jsonify({
        "ok": True,
        "deleted": deleted,
        "errors": errors,
        "ref_removals": ref_removals,
    })


@app.route("/api/gallery/favorite", methods=["POST"])
def toggle_fav():
    d = request.json or {}
    fp = d.get("filepath", "")
    is_fav = state.toggle_favorite(fp)
    return jsonify({"ok": True, "favorite": is_fav})


@app.route("/api/gallery/open-explorer", methods=["POST"])
def open_explorer():
    d = request.json or {}
    fp = d.get("filepath", "")
    if fp and os.path.exists(fp):
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(fp)])
        return jsonify({"ok": True})
    return jsonify({"ok": False})


@app.route("/api/gallery/use-as-ref", methods=["POST"])
def use_as_ref():
    d = request.json or {}
    fp = d.get("filepath", "")
    if not fp or not os.path.exists(fp):
        return jsonify({"ok": False, "error": "File not found"})
    # Give the client a specific reason when the drop silently failed — the
    # add_ref_image() early-outs logged only to server log, so the user never
    # saw WHY nothing happened.
    with state.ref_lock:
        if fp in state.ref_path_list:
            return jsonify({"ok": False, "error": "Already a reference"})
        limit = state.get_ref_limit()
        if len(state.ref_images) >= limit:
            return jsonify({
                "ok": False,
                "error": f"Max {limit} reference images (drop on a slot to replace)",
                "limit_reached": True,
            })
    ok = state.add_ref_image(fp)
    return jsonify({"ok": ok})


@app.route("/api/refs/replace-from-path/<int:idx>", methods=["POST"])
def replace_ref_from_path(idx):
    """Replace the ref at `idx` with an image referenced by filepath (e.g.
    a gallery item dragged onto the cell). JSON body: {filepath}."""
    d = request.json or {}
    fp = d.get("filepath", "")
    if not fp or not os.path.exists(fp):
        return jsonify({"ok": False, "error": "File not found"})
    with state.ref_lock:
        if not (0 <= idx < len(state.ref_path_list)):
            return jsonify({"ok": False, "error": "Invalid slot"})
        # Dropping the same path onto its own cell is a no-op success.
        if state.ref_path_list[idx] == fp:
            return jsonify({"ok": True, "unchanged": True})
        # Prevent creating a duplicate ref by replacing cell A with the path
        # that is already at cell B.
        if fp in state.ref_path_list:
            return jsonify({"ok": False, "error": "Already a reference in another slot"})
    ok = state.replace_ref(idx, fp)
    return jsonify({"ok": ok})


@app.route("/api/gallery/load-setup", methods=["POST"])
def load_setup():
    d = request.json or {}
    fp = d.get("filepath", "")
    item = state.gallery_items.get(fp)
    if not item:
        return jsonify({"ok": False, "error": "Not found"})
    saved = item.get("generation_settings", {})
    if not saved:
        return jsonify({"ok": False, "error": "No saved setup"})

    state.model = saved.get("model", state.model)
    state.aspect = saved.get("aspect", state.aspect)
    state.resolution = saved.get("resolution", state.resolution)
    state.count = int(saved.get("count", 1))
    state.output_dir = saved.get("output_dir", state.output_dir)

    naming = saved.get("naming", {})
    state.naming_enabled = bool(naming.get("enabled"))
    state.naming_prefix = naming.get("prefix", "S010")
    state.naming_delimiter = naming.get("delimiter", "C010")
    state.naming_index_prefix = naming.get("index_prefix", "I")
    state.naming_padding = int(naming.get("padding", 3))

    # Restore refs.
    #
    # Drag-and-drop refs are cached under temp_ref_dir with digest-based names
    # and tracked in state.temp_ref_paths. clear_refs() deletes any such files
    # from disk (so temp refs don't accumulate). If the saved setup references
    # those same paths, the subsequent add_ref_image(rp) would then fail the
    # os.path.exists check. To keep drag-dropped refs loadable, snapshot their
    # bytes before clearing and rewrite the files if clear removed them.
    ref_paths = [p for p in (saved.get("ref_paths") or []) if p]
    pinned = set(p for p in (saved.get("pinned_ref_paths") or []) if p)
    buffered = {}
    for rp in ref_paths:
        if rp in buffered:
            continue
        try:
            if os.path.isfile(rp):
                with open(rp, "rb") as f:
                    buffered[rp] = f.read()
        except Exception:
            pass

    state.clear_refs()

    for rp, data in buffered.items():
        if os.path.exists(rp):
            continue
        try:
            os.makedirs(os.path.dirname(rp), exist_ok=True)
            with open(rp, "wb") as f:
                f.write(data)
            try:
                state.temp_ref_paths.add(rp)
            except Exception:
                pass
        except Exception:
            pass

    for rp in ref_paths:
        if os.path.exists(rp):
            state.add_ref_image(rp, pinned=rp in pinned)

    state.fixed_prompt = saved.get("fixed_prompt", "")
    ps = saved.get("prompt_sections")
    if not ps:
        ps = [saved.get("prompt", "")]
    state.prompt_sections = ps

    state.log(f"Loaded setup from {os.path.basename(fp)}")
    return jsonify({"ok": True})


# --- Generation ---
@app.route("/api/generate", methods=["POST"])
def start_generate():
    if not state.client_vertex and not state.client_studio:
        return jsonify({"ok": False, "error": "No API connected"})

    prompt = state.compose_prompt()
    if not prompt:
        return jsonify({"ok": False, "error": "Empty prompt"})

    providers = state.get_available_providers()
    if not providers:
        return jsonify({"ok": False, "error": "No provider available"})

    os.makedirs(state.output_dir, exist_ok=True)
    naming = state.get_naming_settings()

    count = state.count

    # Check queue capacity (approximate — re-checked atomically in the
    # critical section below; this early-out just avoids the expensive
    # ref snapshot work when the queue is already obviously full).
    outstanding_hint = state.get_queue_outstanding()
    if outstanding_hint + count > state.max_queued_images:
        return jsonify({
            "ok": False,
            "error": f"Queue full ({outstanding_hint}/{state.max_queued_images})",
            "queue_full": True,
        })

    # Snapshot settings for this batch — done outside the lock because
    # ref_image_to_bytes re-encodes images and shouldn't block other callers.
    model = state.model
    aspect = state.aspect
    resolution = state.resolution
    img_cfg = {"aspect_ratio": aspect}
    if "gemini-3" in model:
        img_cfg["image_size"] = resolution
    ref_snapshots = state.get_effective_ref_images(model)
    ref_payloads = [state.ref_image_to_bytes(r) for r in ref_snapshots]
    ref_paths = list(state.ref_path_list)
    pinned_ref_paths = [
        p for i, p in enumerate(state.ref_path_list)
        if i < len(state.ref_pinned) and state.ref_pinned[i]
    ]
    fixed_prompt_snapshot = state.fixed_prompt
    prompt_sections_snapshot = list(state.prompt_sections)

    # --- Atomic critical section -----------------------------------------
    # Pre-v2101 the read of is_generating, the reset of counters, and the
    # set of is_generating=True all lived outside any lock. Two rapid
    # Generate clicks could both see is_generating=False, both reset the
    # counters, and both start a worker thread — double workers racing on
    # pending_jobs caused bogus total/done/outstanding numbers, and the
    # losing worker's finally {pending_jobs.clear()} could delete queued
    # jobs the other worker hadn't picked up yet (the "queue has items but
    # no worker" stall). Everything that reads or writes the shared
    # generator state now happens under pending_jobs_lock.
    with state.pending_jobs_lock:
        # If a Stop is in flight, the previous worker is either still
        # draining active futures or already in its teardown finally. Piling
        # new jobs onto pending_jobs right now would race: the worker's
        # cancel-branch teardown would discard those jobs along with the
        # cancelled ones. Refuse cleanly; the user can retry in a moment.
        if state.cancel_flag:
            return jsonify({
                "ok": False,
                "error": "Stopping previous batch — try again in a moment",
            })

        was_generating = state.is_generating
        # Re-check capacity with the real pending_jobs snapshot.
        current_outstanding = len(state.pending_jobs) + state.active_job_count
        if current_outstanding + count > state.max_queued_images:
            return jsonify({
                "ok": False,
                "error": f"Queue full ({current_outstanding}/{state.max_queued_images})",
                "queue_full": True,
            })

        if not was_generating:
            # Starting a new batch — reset run-scoped counters and claim
            # the generator slot BEFORE releasing the lock so any other
            # request arriving mid-setup sees is_generating=True and takes
            # the "queue into existing batch" branch instead.
            state.queue_count = 0
            state.done_count = 0
            state.fail_count = 0
            state.discarded_count = 0
            state.is_generating = True
            state.cancel_flag = False
            base_idx = 0
        else:
            base_idx = state.queue_count

        new_jobs = []
        for i in range(count):
            new_jobs.append({
                "index": base_idx + i,
                "seed": random.randint(0, 2147483646),
                "preferred_provider": providers[i % len(providers)],
                "prompt": prompt,
                "model": model,
                "aspect": aspect,
                "resolution": resolution,
                "img_cfg": dict(img_cfg),
                "naming": dict(naming),
                "ref_payloads": ref_payloads,
                "ref_paths": ref_paths,
                "pinned_ref_paths": pinned_ref_paths,
                "output_dir": state.output_dir,
                "fixed_prompt": fixed_prompt_snapshot,
                "prompt_sections": prompt_sections_snapshot,
                # Remembered so Load can restore the count the user picked for
                # this batch (per-item gen_settings used to hardcode 1).
                "batch_count": count,
            })

        state.pending_jobs.extend(new_jobs)
        state.queue_count += count
        outstanding_after = len(state.pending_jobs) + state.active_job_count
    # --- End of critical section -----------------------------------------

    if not was_generating:
        # File counter prep scans the output dir; do it outside the lock.
        # The worker thread is spawned after this returns, so there's no
        # race on file_counter.
        state.prepare_file_counter(naming)
        state.log(f"Starting {count} image(s) across {', '.join(state.get_provider_label(p) for p in providers)}")
        threading.Thread(target=state.gen_worker, daemon=True).start()
    else:
        preview = prompt.replace("\n", " ").strip()
        preview = preview[:56] + ("..." if len(preview) > 56 else "")
        state.log(f"Queued {count} image(s) (outstanding {outstanding_after}/{state.max_queued_images}) | {preview}")

    return jsonify({
        "ok": True,
        "count": count,
        "queued": was_generating,
        "outstanding": outstanding_after,
    })


@app.route("/api/stop", methods=["POST"])
def stop_generate():
    if state.is_generating:
        state.cancel_flag = True
        state.log("Stop requested")
    return jsonify({"ok": True})


# --- Project ---
@app.route("/api/project/recent")
def recent_projects():
    entries = state.get_recent_projects()
    return jsonify({"projects": entries})


def _sanitize_project_name(name):
    """Strip illegal Windows filename chars and trim length."""
    name = (name or "").strip()
    if not name:
        return ""
    bad = '<>:"/\\|?*'
    cleaned = "".join(c for c in name if c not in bad and ord(c) >= 32)
    cleaned = cleaned.strip(" .")
    return cleaned[:80]


def _suggest_unique_name(base_name, save_dir):
    """Return a name (no extension) that doesn't collide with an existing
    .json file in save_dir. 'foo' -> 'foo_2' if 'foo.json' exists, else
    'foo_3', etc. Caps at 999; falls back to a timestamp suffix beyond that
    (should never happen in practice but keeps us from looping forever)."""
    for i in range(2, 1000):
        candidate = f"{base_name}_{i}"
        if not os.path.exists(os.path.join(save_dir, f"{candidate}.json")):
            return candidate
    return f"{base_name}_{int(time.time())}"


@app.route("/api/project/new", methods=["POST"])
def new_project():
    """Reset to a blank project. Clears prompts, refs, gallery items, favorites,
    current project path, and naming counter. Does NOT touch output_dir or
    on-disk files (generated images stay on disk; gallery just forgets them)."""
    # Refuse while generation is live — clearing state mid-run would leave
    # the worker writing to freed lists.
    if state.is_generating:
        return jsonify({"ok": False, "error": "Cannot start new project while generating"})

    # Clear refs + close PIL handles (under ref_lock)
    with state.ref_lock:
        for img in state.ref_images:
            try: img.close()
            except Exception: pass
        state.ref_images.clear()
        state.ref_path_list.clear()
        state.ref_pinned.clear()

    # Clear gallery state (under gallery_lock) — but don't delete files
    with state.gallery_lock:
        state.gallery_items.clear()
        state.generated_paths.clear()
        state.favorites.clear()
        state.gallery_order_counter = 0

    # Reset counters that drive the progress bar so the UI starts clean
    state.done_count = 0
    state.fail_count = 0
    state.discarded_count = 0
    state.queue_count = 0

    # Reset prompts + settings to defaults
    state.fixed_prompt = ""
    state.prompt_sections = [""]
    state.model = "gemini-3-pro-image-preview"
    state.aspect = "16:9"
    state.resolution = "4K"
    state.count = 1
    state.naming_enabled = False
    with state.file_counter_lock:
        state.file_counter = 0

    state.current_project_path = None
    state.project_dirty = False
    state.log("New project - cleared workspace")
    return jsonify({"ok": True})


@app.route("/api/project/save", methods=["POST"])
def save_project():
    """Save the current project.

    Conflict handling: if the user typed a name that already exists AND
    they aren't re-saving the currently loaded project, the first call
    returns {ok: False, conflict: True, suggested: "name_2"} without
    writing anything. The frontend then asks the user to pick a strategy
    and re-sends with strategy="overwrite" or strategy="suffix". Previously
    the server silently overwrote any matching filename.
    """
    d = request.json or {}
    fp = d.get("filepath", "")
    name = _sanitize_project_name(d.get("name", ""))
    strategy = (d.get("strategy") or "").lower()  # "", "overwrite", "suffix"

    if not fp:
        save_dir = state.get_project_save_dir()
        os.makedirs(save_dir, exist_ok=True)
        if name:
            target = os.path.join(save_dir, f"{name}.json")
            current = state.current_project_path or ""
            try:
                is_same_as_current = bool(current) and (
                    os.path.normcase(os.path.realpath(target)) ==
                    os.path.normcase(os.path.realpath(current))
                )
            except Exception:
                is_same_as_current = False
            if os.path.exists(target) and not is_same_as_current and strategy not in ("overwrite", "suffix"):
                return jsonify({
                    "ok": False,
                    "conflict": True,
                    "suggested": _suggest_unique_name(name, save_dir),
                    "existing_name": f"{name}.json",
                })
            if os.path.exists(target) and strategy == "suffix" and not is_same_as_current:
                name = _suggest_unique_name(name, save_dir)
                target = os.path.join(save_dir, f"{name}.json")
            fp = target
        elif state.current_project_path and os.path.basename(state.current_project_path):
            # Overwrite existing named project (user hit Save without a name)
            fp = state.current_project_path
        else:
            fp = os.path.join(save_dir, state.default_project_filename())
    try:
        state.save_project(fp)
        state.log(f"Project saved: {os.path.basename(fp)}")
        return jsonify({"ok": True, "filepath": fp, "name": os.path.basename(fp)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:120]})


@app.route("/api/project/load", methods=["POST"])
def load_project():
    d = request.json or {}
    fp = d.get("filepath", "")
    if not fp:
        return jsonify({"ok": False, "error": "No filepath"})
    ok, msg = state.load_project(fp)
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/project/upload", methods=["POST"])
def upload_project():
    f = request.files.get("file")
    if not f:
        return jsonify({"ok": False, "error": "No file"})
    os.makedirs(state.temp_ref_dir, exist_ok=True)
    tmp = os.path.join(state.temp_ref_dir, f"_proj_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    f.save(tmp)
    ok, msg = state.load_project(tmp)
    try:
        os.remove(tmp)
    except Exception:
        pass
    return jsonify({"ok": ok, "message": msg})


# --- File dialog helper: force to foreground on Windows ---
def _make_dialog_root():
    """Create a tkinter root that forces the file dialog to appear on top of Chrome."""
    import tkinter as tk
    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry("0x0+0+0")
    root.attributes("-topmost", True)
    root.update()
    if sys.platform == "win32":
        try:
            import ctypes
            hwnd = root.winfo_id()
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
    root.focus_force()
    return root


# --- Browse folder ---
@app.route("/api/browse-folder", methods=["POST"])
def browse_folder():
    try:
        from tkinter import filedialog
        root = _make_dialog_root()
        initial = state.output_dir if os.path.isdir(state.output_dir) else os.path.expanduser("~")
        folder = filedialog.askdirectory(parent=root, title="Select Output Folder", initialdir=initial)
        root.destroy()
        if folder:
            state.output_dir = folder
            state.project_dirty = True
            return jsonify({"ok": True, "folder": folder})
        return jsonify({"ok": False})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:80]})


@app.route("/api/browse-files", methods=["POST"])
def browse_files():
    try:
        from tkinter import filedialog
        root = _make_dialog_root()
        initial = state.output_dir if os.path.isdir(state.output_dir) else os.path.expanduser("~")
        paths = filedialog.askopenfilenames(
            parent=root,
            title="Select Reference Images",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp")],
            initialdir=initial,
        )
        root.destroy()
        added = 0
        for p in paths:
            if state.add_ref_image(p):
                added += 1
        return jsonify({"ok": True, "added": added})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:80]})


@app.route("/api/browse-project", methods=["POST"])
def browse_project():
    try:
        from tkinter import filedialog
        root = _make_dialog_root()
        # Default to NanoBanana JSON project folder
        project_dir = state.get_project_save_dir()
        if not os.path.isdir(project_dir):
            project_dir = os.path.expanduser("~/Documents")
        if not os.path.isdir(project_dir):
            project_dir = state.output_dir
        fp = filedialog.askopenfilename(
            parent=root,
            title="Load Project",
            filetypes=[("JSON Project", "*.json"), ("All Files", "*.*")],
            initialdir=project_dir,
        )
        root.destroy()
        if fp:
            ok, msg = state.load_project(fp)
            return jsonify({"ok": ok, "message": msg, "filepath": fp})
        return jsonify({"ok": False})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:80]})


@app.route("/api/save-project-as", methods=["POST"])
def save_project_as():
    try:
        from tkinter import filedialog
        root = _make_dialog_root()
        initial_dir = state.get_project_save_dir()
        fp = filedialog.asksaveasfilename(
            parent=root,
            title="Save Project",
            defaultextension=".json",
            initialdir=initial_dir,
            initialfile=state.default_project_filename(),
            filetypes=[("JSON Project", "*.json")],
        )
        root.destroy()
        if fp:
            state.save_project(fp)
            state.log(f"Project saved: {os.path.basename(fp)}")
            return jsonify({"ok": True, "filepath": fp})
        return jsonify({"ok": False})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:80]})


# --- Close / save prompt ---
@app.route("/api/close-requested")
def close_requested():
    """JS polls this to detect when user clicked the X button."""
    requested = state.close_requested
    # Reset after read so the dialog only triggers once per click
    state.close_requested = False
    return jsonify({"close_requested": requested})


@app.route("/api/delete-confirm-state")
def delete_confirm_state():
    return jsonify({"skip": state.skip_delete_confirm})


@app.route("/api/delete-confirm-state", methods=["POST"])
def set_delete_confirm_state():
    d = request.json or {}
    state.skip_delete_confirm = bool(d.get("skip", False))
    state.save_prefs()
    return jsonify({"ok": True, "skip": state.skip_delete_confirm})


@app.route("/api/prompt-history")
def api_prompt_history():
    return jsonify({"history": list(state.prompt_history)})


@app.route("/api/prompt-history", methods=["DELETE"])
def clear_prompt_history():
    state.prompt_history = []
    state.save_prefs()
    return jsonify({"ok": True})


@app.route("/api/close-info")
def close_info():
    has_content = (
        bool(state.gallery_items)
        or bool(state.ref_path_list)
        or bool(state.compose_prompt().strip())
    )
    return jsonify({
        "has_content": has_content,
        "project_dirty": state.project_dirty,
        "current_project": state.current_project_path or "",
        "current_project_name": os.path.basename(state.current_project_path) if state.current_project_path else "",
        "save_dir": state.get_project_save_dir(),
    })


@app.route("/api/close-save", methods=["POST"])
def close_save():
    """Save project (to current path or default location) before closing."""
    try:
        d = request.json or {}
        name = _sanitize_project_name(d.get("name", ""))
        save_dir = state.get_project_save_dir()
        os.makedirs(save_dir, exist_ok=True)
        if name:
            fp = os.path.join(save_dir, f"{name}.json")
        elif state.current_project_path:
            fp = state.current_project_path
        else:
            fp = os.path.join(save_dir, state.default_project_filename())
        state.save_project(fp)
        return jsonify({"ok": True, "filepath": fp})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:120]})


# --- Always on top toggle ---
def _find_nanobanana_hwnd():
    """Find NanoBanana's top-level window by title prefix. Returns 0 if
    not found. Same EnumWindows strategy as launcher._focus_existing_ —
    title changes as projects load ("NanoBanana - foo.json *") but still
    starts with "NanoBanana"."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        found = [0]

        def _cb(hwnd, _lp):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value.startswith("NanoBanana"):
                    found[0] = hwnd
                    return False
            except Exception:
                pass
            return True

        user32.EnumWindows(EnumWindowsProc(_cb), 0)
        return found[0]
    except Exception:
        return 0


def _apply_always_on_top(enabled):
    """Push the HWND to topmost / not-topmost via SetWindowPos, then verify
    the WS_EX_TOPMOST bit on GWL_EXSTYLE actually flipped. Returns
    (ok, err_msg). v2005 returned True without checking either the
    SetWindowPos BOOL return or the actual ex-style, so a silent failure
    (e.g. 64-bit HWND sentinel truncation) looked successful."""
    if sys.platform != "win32":
        return False, "Windows only"
    hwnd = _find_nanobanana_hwnd()
    if not hwnd:
        return False, "Window not found"
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)

        # Explicit argtypes. Without this, Python's default int->arg
        # conversion can truncate negative HWND sentinel values (-1/-2) to
        # 32 bits on 64-bit Windows, which passes a garbage HWND to
        # SetWindowPos and the call silently no-ops. wintypes.HWND is a
        # pointer type, so building wintypes.HWND(-1) yields the correct
        # 0xFFFF...FFFF sentinel regardless of bitness.
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ctypes.c_uint,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL

        HWND_TOPMOST = wintypes.HWND(-1)
        HWND_NOTOPMOST = wintypes.HWND(-2)
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOACTIVATE = 0x0010

        insert_after = HWND_TOPMOST if enabled else HWND_NOTOPMOST
        ok = user32.SetWindowPos(
            wintypes.HWND(hwnd), insert_after, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )
        if not ok:
            err = ctypes.get_last_error()
            return False, f"SetWindowPos err={err}"

        # Verify WS_EX_TOPMOST flipped — the authoritative signal that the
        # window is actually topmost now (some drivers / shell extensions
        # have been known to swallow the flag).
        GWL_EXSTYLE = -20
        WS_EX_TOPMOST = 0x00000008
        # GetWindowLongPtrW is 64-bit safe; fall back to GetWindowLongW.
        get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        try:
            get_long.argtypes = [wintypes.HWND, ctypes.c_int]
            get_long.restype = ctypes.c_ssize_t
        except Exception:
            pass
        ex_style = get_long(wintypes.HWND(hwnd), GWL_EXSTYLE)
        is_topmost = bool(ex_style & WS_EX_TOPMOST)
        if is_topmost != bool(enabled):
            return False, f"style-mismatch(ex=0x{ex_style & 0xFFFFFFFF:08x})"
        return True, ""
    except Exception as e:
        return False, str(e)[:80]


@app.route("/api/always-on-top", methods=["GET"])
def get_always_on_top():
    return jsonify({"enabled": bool(state.always_on_top)})


@app.route("/api/always-on-top", methods=["POST"])
def set_always_on_top():
    d = request.json or {}
    enabled = bool(d.get("enabled"))
    if sys.platform != "win32":
        return jsonify({"ok": False, "error": "Windows only"})
    ok, err = _apply_always_on_top(enabled)
    if not ok:
        state.log(f"Always-on-top toggle failed: {err}")
        return jsonify({"ok": False, "error": err or "Toggle failed"})
    state.always_on_top = enabled
    state.log(f"Always-on-top: {'ON' if enabled else 'OFF'}")
    return jsonify({"ok": True, "enabled": enabled})


# --- UI-driven log line (for Prompt clipboard copy etc.) ---
@app.route("/api/log-message", methods=["POST"])
def log_message():
    d = request.json or {}
    msg = str(d.get("message", "")).strip()
    if not msg:
        return jsonify({"ok": False})
    # Keep the log readable and defensive: cap length, strip newlines, and
    # ASCII-encode (execution strings must be ASCII — see CLAUDE.md rule 11).
    msg = msg.replace("\r", " ").replace("\n", " ")
    if len(msg) > 200:
        msg = msg[:200] + "..."
    try:
        safe = msg.encode("ascii", "replace").decode("ascii")
    except Exception:
        safe = "".join(c if ord(c) < 128 else "?" for c in msg)
    state.log(safe)
    return jsonify({"ok": True})


# --- Clipboard copy ---
@app.route("/api/copy-to-clipboard", methods=["POST"])
def copy_to_clipboard():
    d = request.json or {}
    fp = d.get("filepath", "")
    if not fp or not os.path.exists(fp):
        return jsonify({"ok": False, "error": "File not found"})
    if sys.platform != "win32":
        return jsonify({"ok": False, "error": "Windows only"})

    try:
        import ctypes
        from ctypes import wintypes
        with Image.open(fp) as img:
            image = _to_rgb_flatten(img)
        output = io.BytesIO()
        image.save(output, "BMP")
        data = output.getvalue()[14:]

        GMEM_MOVEABLE = 0x0002
        CF_DIB = 8
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.restype = ctypes.c_void_p
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        user32.CloseClipboard.restype = wintypes.BOOL

        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            raise OSError("GlobalAlloc failed")
        locked = kernel32.GlobalLock(h)
        if not locked:
            kernel32.GlobalFree(h)
            raise OSError("GlobalLock failed")
        ctypes.memmove(locked, data, len(data))
        kernel32.GlobalUnlock(h)

        opened = False
        for _ in range(12):
            if user32.OpenClipboard(None):
                opened = True
                break
            time.sleep(0.03)
        if not opened:
            kernel32.GlobalFree(h)
            raise OSError("OpenClipboard failed")
        try:
            user32.EmptyClipboard()
            if not user32.SetClipboardData(CF_DIB, h):
                raise OSError("SetClipboardData failed")
            h = None
        finally:
            user32.CloseClipboard()
            if h:
                kernel32.GlobalFree(h)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:80]})


# ==========================================
# Startup
# ==========================================
def init_app():
    state.init_api()


def cleanup():
    # Only clean up sensitive credentials on exit.
    # Ref image cache is intentionally preserved so that projects loaded later
    # can still reference the same file paths (e.g. pasted/uploaded clipboard images).
    state.cleanup_vertex_credentials()


atexit.register(cleanup)


if __name__ == "__main__":
    threading.Thread(target=init_app, daemon=True).start()
    print("NanoBanana Web starting on http://127.0.0.1:5656")
    app.run(host="127.0.0.1", port=5656, debug=False, threaded=True)
