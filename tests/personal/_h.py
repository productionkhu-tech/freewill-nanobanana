# -*- coding: utf-8 -*-
"""Shared helpers for the personal-edition tests (plain scripts, no pytest).

isolate(edition) points every user folder at a throwaway directory (CLAUDE.md rule 17) BEFORE anything
from the app is imported. FakeHTTP is a local stand-in for a provider or the gateway: it records every
request (method, path, headers, body) so a test can prove what was - and was not - sent."""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.parse

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
PY = sys.executable
_fails, _n = [], [0]


def check(name, cond, extra=""):
    _n[0] += 1
    print(("ok   " if cond else "FAIL ") + name + (("  " + str(extra)) if extra != "" else ""), flush=True)
    if not cond:
        _fails.append(name)
    return bool(cond)


def done():
    print("\nchecks %d, failed %d" % (_n[0], len(_fails)), flush=True)
    for f in _fails:
        print("  -", f, flush=True)
    sys.exit(1 if _fails else 0)


def isolate(edition="company"):
    """Throwaway data/home/dock folders + the edition switch (source runs only). Returns the paths."""
    work = tempfile.mkdtemp(prefix="nb_t_")
    home = os.path.join(work, "home")
    for sub in ("Documents", "Desktop", "Pictures"):
        os.makedirs(os.path.join(home, sub), exist_ok=True)
    paths = {"work": work, "home": home, "data": os.path.join(work, "data"), "dock": os.path.join(work, "dock")}
    os.environ["NANOBANANA_DATA_DIR"] = paths["data"]
    os.environ["USERPROFILE"] = home
    os.environ["HOME"] = home
    os.environ["NANOBANANA_MINI_DOCK_DIR"] = paths["dock"]
    if edition == "personal":
        os.environ["NANOBANANA_EDITION"] = "personal"
    else:
        os.environ.pop("NANOBANANA_EDITION", None)
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    return paths


def run_py(edition, code, env_extra=None, timeout=180):
    """Run `code` in a fresh interpreter after isolate(edition); the code prints one JSON line last.
    Returns that JSON (or {"_error": stderr tail})."""
    pre = ("import sys, json, os; sys.path.insert(0, %r); sys.path.insert(0, %r)\n"
           "from _h import isolate; P = isolate(%r)\n") % (REPO, os.path.dirname(os.path.abspath(__file__)), edition)
    env = {k: v for k, v in os.environ.items() if not k.startswith("_MEI")}
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    env.pop("NANOBANANA_EDITION", None)
    if env_extra:
        env.update(env_extra)
    r = subprocess.run([PY, "-c", pre + code], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=timeout, cwd=REPO)
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    try:
        return json.loads(lines[-1])
    except Exception:
        return {"_error": (r.stderr or r.stdout)[-1500:]}


class _Req:
    def __init__(self, method, raw_path, headers, body):
        u = urllib.parse.urlsplit(raw_path)
        self.method, self.path, self.raw_path = method, u.path, raw_path
        self.query = dict(urllib.parse.parse_qsl(u.query))
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.raw = body
        try:
            self.body = json.loads(body.decode("utf-8")) if body else None
        except Exception:
            self.body = None


class FakeHTTP:
    """routes: {(METHOD, "/path"): fn(req) -> (status, obj)}. Unknown routes answer 404."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _do(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                req = _Req(self.command, self.path, dict(self.headers), body)
                outer.calls.append(req)
                fn = outer.routes.get((self.command, req.path))
                status, obj = fn(req) if fn else (404, {"error": "not found"})
                data = json.dumps(obj).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_DELETE = _do

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        self.srv.shutdown()
        self.srv.server_close()
