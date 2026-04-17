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
        self.vertex_rate_limiter = RateLimiter(interval=0.5)
        self.studio_rate_limiter = RateLimiter(interval=0.5)
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
        self.skip_delete_confirm = False
        self.output_dir = os.path.join(os.path.expanduser("~"), "Desktop", "NanoBanana_Output")
        self.file_counter = 0

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

        # Skip delete confirm for session
        self.skip_delete_confirm = False

        # Close-requested flag (set by launcher when user clicks X)
        self.close_requested = False

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

    # --- API ---
    def cleanup_vertex_credentials(self):
        pass

    def init_api(self):
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
                return Image.open(io.BytesIO(part.inline_data.data)).convert("RGB")
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
        return list(self.ref_images[:limit])

    def get_effective_ref_paths(self, model=None):
        limit = self.get_ref_limit(model)
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
        if filepath in self.ref_path_list:
            self.log(f"Ref already added: {os.path.basename(filepath)}")
            return False
        limit = self.get_ref_limit()
        if len(self.ref_images) >= limit:
            self.log(f"Max {limit} reference images")
            return False
        try:
            with Image.open(filepath) as img:
                pil = img.convert("RGB")
            self.ref_images.append(pil)
            self.ref_path_list.append(filepath)
            self.ref_pinned.append(bool(pinned))
            self.project_dirty = True
            return True
        except Exception as e:
            self.log(f"Ref load failed: {str(e)[:80]}")
            return False

    def remove_ref(self, idx):
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

    def toggle_ref_pin(self, idx):
        if not (0 <= idx < len(self.ref_path_list)):
            return
        while len(self.ref_pinned) < len(self.ref_path_list):
            self.ref_pinned.append(False)
        self.ref_pinned[idx] = not self.ref_pinned[idx]
        self.project_dirty = True

    def clear_refs(self, preserve_pinned=False):
        kept_imgs, kept_paths, kept_pinned = [], [], []
        removed = []
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
        for fp in removed:
            self.cleanup_temp_ref_path(fp)
        self.project_dirty = True

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
            clip.convert("RGB").save(fp, "PNG")
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
            self.file_counter += 1
            num = str(self.file_counter).zfill(s["padding"])
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
            self.file_counter = 0
            return
        pattern = re.compile(
            rf"^{re.escape(s['prefix'])}"
            rf"(?:_{re.escape(s['delimiter'])})?"
            rf"_{re.escape(s.get('index_prefix', ''))}(\d+)\.png$",
            re.IGNORECASE,
        )
        max_num = 0
        try:
            for name in os.listdir(self.output_dir):
                m = pattern.match(name)
                if m:
                    max_num = max(max_num, int(m.group(1)))
        except OSError:
            pass
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

    def delete_gallery_item(self, filepath):
        if filepath in self.favorites:
            return False, "Unfavorite first", None
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
            if filepath in self.generated_paths:
                self.generated_paths.remove(filepath)
            self.favorites.discard(filepath)
            self.gallery_items.pop(filepath, None)
            # Also remove from refs if present
            removed_ref_idx = None
            ref_count_before = len(self.ref_path_list)
            if filepath in self.ref_path_list:
                removed_ref_idx = self.ref_path_list.index(filepath)
                self.remove_ref(removed_ref_idx)
            self.project_dirty = True
            return True, "Deleted", {"removed_ref_idx": removed_ref_idx, "ref_count_before": ref_count_before}
        except Exception as e:
            return False, str(e)[:80], None

    def toggle_favorite(self, filepath):
        if filepath in self.favorites:
            self.favorites.discard(filepath)
            if filepath in self.gallery_items:
                self.gallery_items[filepath]["favorite"] = False
            return False
        else:
            self.favorites.add(filepath)
            if filepath in self.gallery_items:
                self.gallery_items[filepath]["favorite"] = True
            return True

    def prune_missing_files(self):
        missing = [p for p in list(self.gallery_items.keys()) if not os.path.exists(p)]
        for fp in missing:
            self.favorites.discard(fp)
            if fp in self.generated_paths:
                self.generated_paths.remove(fp)
            self.gallery_items.pop(fp, None)
            if fp in self.ref_path_list:
                idx = self.ref_path_list.index(fp)
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
        pinned_ref_paths = [
            p for i, p in enumerate(self.ref_path_list)
            if i < len(self.ref_pinned) and self.ref_pinned[i] and p and os.path.exists(p)
        ]
        items = [
            self._serialize_item(fp, item)
            for fp, item in sorted(self.gallery_items.items(), key=lambda x: x[1].get("order", 0))
        ]
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
            "logs": "\n".join(self.logs),
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
        data = self.collect_project_state()
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
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
        self.count = int(ui.get("count", self.count))
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
        self.naming_padding = int(naming.get("padding", 3))

        self.gallery_columns = int(ui.get("gallery_columns", 2))

        # Clear and restore refs
        self.clear_refs()
        ref_paths = [p for p in (ui.get("ref_paths") or []) if p]
        pinned = set(p for p in (ui.get("pinned_ref_paths") or []) if p)
        for rp in ref_paths:
            if os.path.exists(rp):
                self.add_ref_image(rp, pinned=rp in pinned)

        # Clear and restore gallery
        self.gallery_items.clear()
        self.generated_paths.clear()
        self.favorites.clear()
        self.gallery_order_counter = 0

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
            # Always reset state so the user can try again
            self.is_generating = False
            self.cancel_flag = False
            with self.pending_jobs_lock:
                self.pending_jobs.clear()
                self.active_job_count = 0
            self.push_event({"type": "done", "done": self.done_count, "failed": self.fail_count})

    def _gen_worker_body(self):
        max_workers = self.max_parallel_requests
        active = {}

        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="nano-gen") as executor:
            while True:
                # Fill workers from queue
                while not self.cancel_flag and len(active) < max_workers:
                    job = self._pop_pending_job()
                    if job is None:
                        break
                    try:
                        fut = executor.submit(
                            self.generate_one_image,
                            {"index": job["index"], "total": 0,
                             "seed": job["seed"], "preferred_provider": job["preferred_provider"]},
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
                        gen_settings = {
                            "prompt": prompt,
                            "fixed_prompt": self.fixed_prompt,
                            "prompt_sections": list(self.prompt_sections),
                            "model": model, "aspect": aspect, "resolution": resolution,
                            "count": 1, "output_dir": job["output_dir"],
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
state = AppState()


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


@app.route("/")
def index():
    html = render_template("index.html")
    html = html.replace("__VERSION__", _read_version() + "." + _BUILD_ID)
    # Disable browser cache on the main HTML so fresh asset query strings load.
    resp = Response(html)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.route("/viewer")
def viewer():
    """Standalone image viewer window (opened via pywebview create_window)."""
    html = render_template("viewer.html")
    html = html.replace("__VERSION__", _read_version() + "." + _BUILD_ID)
    resp = Response(html)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.route("/prompt-popup")
def prompt_popup():
    """Standalone prompt display window."""
    html = render_template("prompt_popup.html")
    html = html.replace("__VERSION__", _read_version() + "." + _BUILD_ID)
    resp = Response(html)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.route("/api/version")
def api_version():
    return jsonify({"version": _read_version()})


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


@app.route("/api/settings", methods=["POST"])
def update_settings():
    d = request.json or {}
    for k in ("model", "aspect", "resolution", "fixed_prompt",
              "naming_prefix", "naming_delimiter", "naming_index_prefix"):
        if k in d:
            setattr(state, k, d[k])
    if "count" in d:
        state.count = max(1, int(d["count"]))
    if "output_dir" in d:
        state.output_dir = d["output_dir"]
    if "naming_enabled" in d:
        state.naming_enabled = bool(d["naming_enabled"])
    if "naming_padding" in d:
        state.naming_padding = max(1, min(5, int(d["naming_padding"])))
    if "prompt_sections" in d:
        state.prompt_sections = list(d["prompt_sections"])
    if "gallery_columns" in d:
        state.gallery_columns = int(d["gallery_columns"])
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


@app.route("/api/browse-replace-ref", methods=["POST"])
def browse_replace_ref():
    d = request.json or {}
    idx = d.get("index", -1)
    if idx < 0 or idx >= len(state.ref_images):
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
        # Replace the ref at index
        try:
            with Image.open(fp) as img:
                pil = img.convert("RGB")
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:80]})
        old_path = state.ref_path_list[idx]
        old_img = state.ref_images[idx]
        state.ref_images[idx] = pil
        state.ref_path_list[idx] = fp
        try:
            old_img.close()
        except Exception:
            pass
        if old_path != fp:
            state.cleanup_temp_ref_path(old_path)
        state.project_dirty = True
        state.log(f"Replaced ref {idx + 1}: {os.path.basename(fp)}")
        return jsonify({"ok": True})
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
    pinned = idx < len(state.ref_pinned) and state.ref_pinned[idx]
    return jsonify({"ok": True, "pinned": pinned})


@app.route("/api/refs/paste", methods=["POST"])
def paste_ref():
    ok, msg = state.paste_clipboard_ref()
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/refs/thumb/<int:idx>")
def ref_thumb(idx):
    if idx < 0 or idx >= len(state.ref_images):
        return "", 404
    pil = state.ref_images[idx]
    thumb = pil.copy()
    thumb.thumbnail((100, 100), Image.LANCZOS)
    buf = io.BytesIO()
    thumb.save(buf, "PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


# --- Gallery ---
@app.route("/api/gallery")
def get_gallery():
    state.prune_missing_files()
    items = []
    for fp, item in sorted(state.gallery_items.items(), key=lambda x: x[1].get("order", 0)):
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


@app.route("/api/gallery/image")
def serve_gallery_image():
    fp = request.args.get("path", "")
    if not fp or not os.path.exists(fp):
        return "", 404
    return send_file(fp, mimetype="image/png")


@app.route("/api/gallery/thumb")
def serve_gallery_thumb():
    fp = request.args.get("path", "")
    size = int(request.args.get("size", 360))
    if not fp or not os.path.exists(fp):
        return "", 404
    try:
        with Image.open(fp) as img:
            pil = img.convert("RGB")
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
        return jsonify({"ok": False})
    ok = state.add_ref_image(fp)
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

    # Restore refs
    state.clear_refs()
    ref_paths = [p for p in (saved.get("ref_paths") or []) if p]
    pinned = set(p for p in (saved.get("pinned_ref_paths") or []) if p)
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

    was_generating = state.is_generating
    count = state.count

    # Check queue capacity
    outstanding = state.get_queue_outstanding()
    if outstanding + count > state.max_queued_images:
        return jsonify({
            "ok": False,
            "error": f"Queue full ({outstanding}/{state.max_queued_images})",
            "queue_full": True,
        })

    # Snapshot settings for this batch
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

    base_idx = state.queue_count if was_generating else 0
    if not was_generating:
        state.prepare_file_counter(naming)
        state.queue_count = 0
        state.done_count = 0
        state.fail_count = 0
        state.discarded_count = 0

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
            "fixed_prompt": state.fixed_prompt,
            "prompt_sections": list(state.prompt_sections),
        })

    with state.pending_jobs_lock:
        state.pending_jobs.extend(new_jobs)
    state.queue_count += count

    if not was_generating:
        state.is_generating = True
        state.cancel_flag = False
        state.log(f"Starting {count} image(s) across {', '.join(state.get_provider_label(p) for p in providers)}")
        threading.Thread(target=state.gen_worker, daemon=True).start()
    else:
        preview = prompt.replace("\n", " ").strip()
        preview = preview[:56] + ("..." if len(preview) > 56 else "")
        state.log(f"Queued {count} image(s) (outstanding {state.get_queue_outstanding()}/{state.max_queued_images}) | {preview}")

    return jsonify({
        "ok": True,
        "count": count,
        "queued": was_generating,
        "outstanding": state.get_queue_outstanding(),
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


@app.route("/api/project/new", methods=["POST"])
def new_project():
    """Reset to a blank project. Clears prompts, refs, gallery items, favorites,
    current project path, and naming counter. Does NOT touch output_dir or
    on-disk files (generated images stay on disk; gallery just forgets them)."""
    # Clear refs + close PIL handles
    for img in state.ref_images:
        try: img.close()
        except Exception: pass
    state.ref_images.clear()
    state.ref_path_list.clear()
    state.ref_pinned.clear()

    # Clear gallery state (but don't delete files)
    state.gallery_items.clear()
    state.generated_paths.clear()
    state.favorites.clear()
    state.gallery_order_counter = 0

    # Reset prompts + settings to defaults
    state.fixed_prompt = ""
    state.prompt_sections = [""]
    state.model = "gemini-3-pro-image-preview"
    state.aspect = "16:9"
    state.resolution = "4K"
    state.count = 1
    state.naming_enabled = False
    state.file_counter = 0

    state.current_project_path = None
    state.project_dirty = False
    state.log("New project — cleared workspace")
    return jsonify({"ok": True})


@app.route("/api/project/save", methods=["POST"])
def save_project():
    d = request.json or {}
    fp = d.get("filepath", "")
    name = _sanitize_project_name(d.get("name", ""))
    if not fp:
        save_dir = state.get_project_save_dir()
        os.makedirs(save_dir, exist_ok=True)
        if name:
            fp = os.path.join(save_dir, f"{name}.json")
        elif state.current_project_path and os.path.basename(state.current_project_path):
            # Overwrite existing named project
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
    return jsonify({"ok": True, "skip": state.skip_delete_confirm})


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
            image = img.convert("RGB")
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
