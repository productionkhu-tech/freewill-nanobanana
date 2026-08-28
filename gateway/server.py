#!/usr/bin/env python3
"""NanoBanana key gateway.

Why this exists
---------------
The provider keys used to be installed on every employee machine. Rotating a
key meant reinstalling on ~70 desktops, someone who left kept a working key
forever, and 70 clients each throttling themselves to 8 RPM still hammered a
10 RPM account. This server holds the keys instead:

    app  --(personal token)-->  gateway  --(real provider key)-->  OpenAI

The provider key never leaves this process. A personal token is worth only what
this gateway allows, and cutting one person off is a one-line change here.

Enrollment
----------
Handing 70 people a token is the same distribution problem again, so the app
enrolls itself: it presents a TICKET everyone already has (the retired OpenAI
key still sitting in their environment) plus who and which machine it is, and
gets a token of its own back. Tickets are compared by SHA-256, so the ticket
value is never stored here. Close the window once everyone is in.

Run
---
    set NB_GW_OPENAI_KEY=sk-svcacct-...
    set NB_GW_ADMIN_KEY=<something long>
    set NB_GW_TICKET_HASHES=<sha256 of the old key>
    python server.py
"""
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request

from flask import Flask, Response, jsonify, request

APP_NAME = "nanobanana-gateway"


def _base_dir():
    """Folder the gateway lives in — next to the EXE once frozen."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _load_config():
    """Read gateway_config.json sitting next to this program.

    The always-on box has no shell to export variables from, so the installer
    writes a file instead. Environment variables still win, which keeps the
    local test setup working unchanged."""
    path = os.path.join(_base_dir(), "gateway_config.json")
    try:
        # utf-8-sig: the installer writes this file from a shell, and a stray
        # BOM would otherwise make json.load fail and the gateway start with no
        # key at all — silently, on the machine nobody is looking at.
        with open(path, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
    except FileNotFoundError:
        return path, False
    except Exception as e:
        print("[%s] config file is unreadable: %s" % (APP_NAME, str(e)[:120]))
        print("[%s]   %s" % (APP_NAME, path))
        return path, False
    pairs = (
        ("openai_key", "NB_GW_OPENAI_KEY"),
        ("admin_key", "NB_GW_ADMIN_KEY"),
        ("ticket_hashes", "NB_GW_TICKET_HASHES"),
        ("data_dir", "NB_GW_DATA"),
        ("port", "NB_GW_PORT"),
        ("host", "NB_GW_HOST"),
        ("openai_rpm", "NB_GW_OPENAI_RPM"),
        ("max_inflight", "NB_GW_MAX_INFLIGHT"),
        ("github_token", "NB_GW_GITHUB_TOKEN"),
        ("repo", "NB_GW_REPO"),
    )
    for key, env in pairs:
        val = cfg.get(key)
        if isinstance(val, (list, tuple)):
            val = ",".join(str(v) for v in val)
        if val not in (None, "") and not os.environ.get(env):
            os.environ[env] = str(val)
    return path, True


_CONFIG_PATH, _CONFIG_FOUND = _load_config()
UPSTREAM = os.environ.get("NB_GW_UPSTREAM", "https://api.openai.com").rstrip("/")

# Only these upstream paths may be reached. A forward-anything proxy would also
# expose the account billing and key-management endpoints.
ALLOWED_PATHS = {
    "/v1/images/generations",
    "/v1/images/edits",
    "/v1/models",
}

DATA_DIR = os.environ.get("NB_GW_DATA") or os.path.join(
    os.path.expanduser("~"), ".nanobanana-gateway")
TOKENS_FILE = os.path.join(DATA_DIR, "tokens.json")
USAGE_FILE = os.path.join(DATA_DIR, "usage.jsonl")

# Refused before anything is read into memory. Providers cap reference images at
# 30MB each; ten of those plus multipart overhead is the realistic ceiling.
MAX_BODY = int(os.environ.get("NB_GW_MAX_BODY", str(340 * 1024 * 1024)))

# One shared queue for the whole company. Each client still throttles itself,
# but 70 clients doing that independently is not a rate limit.
_RPM = float(os.environ.get("NB_GW_OPENAI_RPM", "60"))
_MAX_INFLIGHT = int(os.environ.get("NB_GW_MAX_INFLIGHT", "12"))

app = Flask(APP_NAME)
app.config["MAX_CONTENT_LENGTH"] = MAX_BODY

_lock = threading.Lock()
_state = {"tokens": {}, "enroll_open": True}
_inflight = threading.Semaphore(_MAX_INFLIGHT)


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------
def _load():
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(TOKENS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        _state["tokens"] = d.get("tokens", {})
        _state["enroll_open"] = bool(d.get("enroll_open", True))
    except Exception:
        _state["tokens"] = {}
        _state["enroll_open"] = True


def _save_locked():
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = TOKENS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"tokens": _state["tokens"], "enroll_open": _state["enroll_open"]},
                  f, ensure_ascii=False, indent=2)
    os.replace(tmp, TOKENS_FILE)


def _usage(**row):
    row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(USAGE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------
# rate limit
# --------------------------------------------------------------------------
class RateLimiter:
    """Token bucket shared by every client. Requests wait their turn here
    instead of racing each other into a 429."""

    def __init__(self, rpm):
        self.interval = 60.0 / max(1.0, rpm)
        self.lock = threading.Lock()
        self.next_at = 0.0

    def acquire(self, timeout=180.0):
        deadline = time.time() + timeout
        while True:
            with self.lock:
                now = time.time()
                if now >= self.next_at:
                    self.next_at = max(now, self.next_at) + self.interval
                    return 0.0
                wait = self.next_at - now
            if time.time() + wait > deadline:
                return -1.0
            time.sleep(min(wait, 0.25))


_limiter = RateLimiter(_RPM)

# Enrolment is the one route a stranger can reach without any credential. The
# ticket is a 160-character key so guessing it is hopeless, but an open loop
# still costs CPU and fills the log; slow down a source that keeps failing.
_enroll_fails = {}
_enroll_lock = threading.Lock()
_ENROLL_MAX_FAILS = 10
_ENROLL_WINDOW = 600.0


def _enroll_blocked(who):
    now = time.time()
    with _enroll_lock:
        hits = [t for t in _enroll_fails.get(who, []) if now - t < _ENROLL_WINDOW]
        _enroll_fails[who] = hits
        return len(hits) >= _ENROLL_MAX_FAILS


def _enroll_failed(who):
    with _enroll_lock:
        _enroll_fails.setdefault(who, []).append(time.time())


def _client_id():
    return (request.headers.get("CF-Connecting-IP")
            or (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            or request.remote_addr or "?")


# --------------------------------------------------------------------------
# auth
# --------------------------------------------------------------------------
def _sha(s):
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def _ticket_hashes():
    raw = os.environ.get("NB_GW_TICKET_HASHES", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _clean(s):
    """Trim what a copy-paste drags along. A BOM or a trailing newline makes an
    otherwise correct ticket hash to something else, and the only symptom is a
    403 that looks like the wrong ticket."""
    return (s or "").replace("﻿", "").strip()


def _ticket_ok(ticket):
    ticket = _clean(ticket)
    if not ticket:
        return False
    got = _sha(ticket)
    return any(hmac.compare_digest(got, h) for h in _ticket_hashes())


def _is_local():
    """Behind a tunnel every request arrives from 127.0.0.1, so this cannot be
    used to tell insiders from outsiders — cloudflared forwards the real client
    in CF-Connecting-IP. Anything carrying that header came from the internet."""
    if request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For"):
        return False
    return request.remote_addr in ("127.0.0.1", "::1")


def _admin_ok():
    # Admin routes stay on the machine itself. Once the gateway is reachable
    # from the internet, an admin key is the only thing between a stranger and
    # the list of everyone enrolled — do not rely on a single secret for that.
    if not _is_local():
        return False
    want = os.environ.get("NB_GW_ADMIN_KEY", "")
    got = (request.headers.get("X-Admin-Key")
           or request.args.get("admin_key") or "")
    return bool(want) and hmac.compare_digest(got, want)


def _bearer():
    h = request.headers.get("Authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else ""


def _token_record(token):
    if not token:
        return None
    tid = _sha(token)[:16]
    with _lock:
        rec = _state["tokens"].get(tid)
        if rec and not rec.get("revoked"):
            return dict(rec, token_id=tid)
    return None


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
@app.get("/health")
def health():
    # A stranger who finds this address learns nothing: how many people are
    # enrolled, whether the enrolment window is open and whether a key is
    # loaded are all useful to an attacker deciding whether to bother. The
    # full picture is for the machine itself.
    if not _is_local():
        return jsonify({"ok": True})
    with _lock:
        n = sum(1 for r in _state["tokens"].values() if not r.get("revoked"))
        openv = _state["enroll_open"]
    return jsonify({"ok": True, "service": APP_NAME,
                    "active_tokens": n, "enroll_open": openv,
                    "upstream_key_loaded": bool(os.environ.get("NB_GW_OPENAI_KEY"))})


@app.get("/")
def root():
    # Nothing here identifies the company or what this proxies.
    return jsonify({"ok": True}), 404


@app.post("/enroll")
def enroll():
    """Exchange a ticket everyone already has for a token of your own."""
    d = request.get_json(silent=True) or {}
    ticket = d.get("ticket", "")
    user = str(d.get("user", ""))[:64] or "unknown"
    machine = str(d.get("machine", ""))[:64] or "unknown"
    version = str(d.get("app_version", ""))[:32]

    with _lock:
        closed = not _state["enroll_open"]
    if closed:
        _usage(event="enroll_closed", user=user, machine=machine)
        return jsonify({"ok": False, "error": "enrollment is closed"}), 423

    who = _client_id()
    if _enroll_blocked(who):
        _usage(event="enroll_throttled", user=user, machine=machine, ip=who)
        return jsonify({"ok": False, "error": "too many attempts, try later"}), 429

    if not _ticket_ok(ticket):
        _enroll_failed(who)
        _usage(event="enroll_bad_ticket", user=user, machine=machine, ip=who)
        return jsonify({"ok": False, "error": "ticket not accepted"}), 403

    # Reinstalling the app, or rerunning the installer, must not mint an endless
    # list of tokens for the same person.
    ident = _sha(user + "|" + machine)[:16]
    with _lock:
        for tid, rec in _state["tokens"].items():
            if rec.get("ident") == ident and not rec.get("revoked"):
                _usage(event="enroll_existing", user=user, machine=machine, token_id=tid, ip=who)
                return jsonify({"ok": True, "token": rec["token"], "token_id": tid,
                                "reused": True})
        token = "nbt_" + secrets.token_hex(24)
        tid = _sha(token)[:16]
        _state["tokens"][tid] = {
            "token": token, "ident": ident, "user": user, "machine": machine,
            "app_version": version, "issued_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "calls": 0, "revoked": False,
        }
        _save_locked()
    _usage(event="enroll_new", user=user, machine=machine, token_id=tid, ip=who)
    return jsonify({"ok": True, "token": token, "token_id": tid, "reused": False})


def _forward(path):
    """Replay the request upstream with the real key attached.

    The body goes byte-for-byte: /v1/images/generations is JSON and
    /v1/images/edits is multipart, and re-encoding either would just be a second
    place for bugs to live."""
    key = os.environ.get("NB_GW_OPENAI_KEY", "")
    if not key:
        return jsonify({"error": {"message": "gateway has no upstream key"}}), 503

    body = request.get_data()
    headers = {"Authorization": "Bearer " + key}
    ct = request.headers.get("Content-Type")
    if ct:
        headers["Content-Type"] = ct
    req = urllib.request.Request(UPSTREAM + path, data=body or None,
                                 headers=headers, method=request.method)
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return Response(r.read(), status=r.status,
                            content_type=r.headers.get("Content-Type", "application/json"))
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code,
                        content_type=e.headers.get("Content-Type", "application/json"))
    except Exception as e:
        return jsonify({"error": {"message": "gateway upstream error: " + str(e)[:200]}}), 502


@app.route("/v1/<path:sub>", methods=["GET", "POST"])
def proxy(sub):
    path = "/v1/" + sub
    if path not in ALLOWED_PATHS:
        return jsonify({"error": {"message": "path not allowed: " + path}}), 404

    rec = _token_record(_bearer())
    if rec is None:
        return jsonify({"error": {"message": "invalid or revoked NanoBanana token",
                                  "code": "invalid_token"}}), 401

    if not _inflight.acquire(timeout=300):
        return jsonify({"error": {"message": "gateway busy"}}), 503
    t0 = time.time()
    req_bytes = len(request.get_data())
    try:
        if _limiter.acquire() < 0:
            return jsonify({"error": {"message": "rate limit wait timed out"}}), 429
        resp = _forward(path)
    finally:
        _inflight.release()

    status = resp[1] if isinstance(resp, tuple) else resp.status_code
    with _lock:
        r = _state["tokens"].get(rec["token_id"])
        if r is not None:
            r["calls"] = r.get("calls", 0) + 1
            r["last_used"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            _save_locked()
    _usage(event="call", token_id=rec["token_id"], user=rec["user"],
           machine=rec["machine"], path=path, status=status,
           elapsed=round(time.time() - t0, 1), req_bytes=req_bytes)
    return resp


# --------------------------------------------------------------------------
# admin
# --------------------------------------------------------------------------
@app.get("/admin/tokens")
def admin_tokens():
    if not _admin_ok():
        return jsonify({"ok": False, "error": "admin key required"}), 403
    with _lock:
        rows = [{"token_id": tid, "user": r["user"], "machine": r["machine"],
                 "issued_at": r["issued_at"], "last_used": r.get("last_used"),
                 "calls": r.get("calls", 0), "revoked": bool(r.get("revoked")),
                 "app_version": r.get("app_version", "")}
                for tid, r in _state["tokens"].items()]
    rows.sort(key=lambda x: x["issued_at"])
    return jsonify({"ok": True, "count": len(rows), "tokens": rows})


@app.post("/admin/revoke")
def admin_revoke():
    if not _admin_ok():
        return jsonify({"ok": False, "error": "admin key required"}), 403
    d = request.get_json(silent=True) or {}
    tid = d.get("token_id", "")
    with _lock:
        rec = _state["tokens"].get(tid)
        if not rec:
            return jsonify({"ok": False, "error": "no such token"}), 404
        rec["revoked"] = True
        rec["revoked_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _save_locked()
    _usage(event="revoke", token_id=tid)
    return jsonify({"ok": True, "token_id": tid})


@app.post("/admin/enrollment")
def admin_enrollment():
    """Open or close the enrollment window. Left open, anyone who learns the
    ticket can mint a token, so close it once everyone is in."""
    if not _admin_ok():
        return jsonify({"ok": False, "error": "admin key required"}), 403
    d = request.get_json(silent=True) or {}
    with _lock:
        _state["enroll_open"] = bool(d.get("open", False))
        _save_locked()
        cur = _state["enroll_open"]
    _usage(event="enrollment_window", open=cur)
    return jsonify({"ok": True, "enroll_open": cur})


def _lan_addresses():
    """Every address this machine can be reached at, best guess first."""
    import socket
    host = socket.gethostname()
    out = []
    try:
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")) and ip not in out:
                out.append(ip)
    except Exception:
        pass
    return host, out


def _safe_stdout():
    """A Korean Windows console is cp949; anything outside it would raise on
    print and take the whole report down."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _print_info():
    _safe_stdout()
    """Everything the admin needs, printed by the program that actually knows it.

    The installer used to dig these values out with nested shell calls, which
    came back empty on a machine whose console codepage did not agree with the
    script. Python has no such trouble."""
    port = int(os.environ.get("NB_GW_PORT", "8787"))
    host, ips = _lan_addresses()
    admin = os.environ.get("NB_GW_ADMIN_KEY", "")
    key = os.environ.get("NB_GW_OPENAI_KEY", "")
    _load()
    with _lock:
        live = sum(1 for r in _state["tokens"].values() if not r.get("revoked"))
        openv = _state["enroll_open"]
    print("")
    print("=" * 62)
    print("  NanoBanana Gateway")
    print("=" * 62)
    print("")
    # A non-ASCII computer name cannot go in a URL, and a Korean console mangles
    # it on the way out — recommend an IP on those machines instead.
    host_ok = bool(host) and host.isascii()
    if host_ok:
        print("  [ADDRESS]  http://%s:%d      <-- use this one" % (host, port))
        for ip in ips:
            print("             http://%s:%d" % (ip, port))
    else:
        for i, ip in enumerate(ips):
            tail = "      <-- use this one" if i == 0 else ""
            print("  [ADDRESS]  http://%s:%d%s" % (ip, port, tail))
        if not ips:
            print("  [ADDRESS]  (no network address found)")
        print("")
        print("  NOTE: this PC name is not usable in a URL (non-English),")
        print("        so use the IP above. Reserve it in the router so it")
        print("        does not change.")
    print("")
    print("  [ADMIN KEY]  %s" % (admin or "(NOT SET - reinstall)"))
    print("")
    print("  config file : %s" % (_CONFIG_PATH if _CONFIG_FOUND else "(NOT FOUND)"))
    print("  data dir    : %s" % DATA_DIR)
    print("  GPT key     : %s" % ("loaded (%d chars)" % len(key) if key else "MISSING - reinstall"))
    print("  tickets     : %d" % len(_ticket_hashes()))
    print("  enrolled    : %d person(s)" % live)
    print("  enrollment  : %s" % ("OPEN" if openv else "closed"))
    print("")
    # Is a copy already serving?
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % port, timeout=4) as r:
            r.read()
        print("  [RUNNING]  the gateway is up and answering")
    except Exception:
        print("  [STOPPED]  nothing is answering on port %d yet" % port)
    print("")
    print("  people enrolled so far:")
    print("    curl -H \"X-Admin-Key: %s\" http://127.0.0.1:%d/admin/tokens"
          % (admin or "<admin key>", port))
    print("")
    print("=" * 62)


def _detect_address():
    """Best public address this machine can be reached at, in preference order:
    an active tunnel first, then a LAN address. The tunnel one is what survives
    a router swap or an office move."""
    import subprocess
    port = int(os.environ.get("NB_GW_PORT", "8787"))
    # Tailscale Funnel, if it is running
    for exe in (os.path.join(os.environ.get("ProgramFiles", ""), "Tailscale", "tailscale.exe"),
                "tailscale"):
        try:
            r = subprocess.run([exe, "funnel", "status"], capture_output=True,
                               text=True, timeout=15)
            for line in (r.stdout or "").splitlines():
                line = line.strip()
                if line.startswith("https://") and ".ts.net" in line:
                    return line.split()[0].rstrip("/"), "tunnel"
        except Exception:
            continue
    host, ips = _lan_addresses()
    if ips:
        return "http://%s:%d" % (ips[0], port), "lan"
    return "", "none"


def _publish(address=""):
    """Write the address into the file every app reads at launch.

    Doing it from here means moving the gateway never involves editing anything
    by hand: this machine knows where it is, and tells everyone."""
    _safe_stdout()
    repo = os.environ.get("NB_GW_REPO", "productionkhu-tech/freewill-nanobanana")
    path = os.environ.get("NB_GW_ENDPOINT_PATH", "gateway_endpoint.txt")
    token = os.environ.get("NB_GW_GITHUB_TOKEN", "")

    if not address:
        address, kind = _detect_address()
        print("[detected] %s  (%s)" % (address or "(none)", kind))
    if not address.startswith(("http://", "https://")):
        print("[ERROR] no usable address found. Start the tunnel first.")
        return 1

    body = chr(10).join([
        "# NanoBanana gateway address. One line, nothing else.",
        "# Published by the gateway machine on " + time.strftime("%Y-%m-%d %H:%M"),
        address,
        "",
    ])

    if not token:
        print("")
        print("  ADDRESS TO PUBLISH:")
        print("    %s" % address)
        print("")
        print("  No GitHub token configured, so nothing was uploaded.")
        print("  Add \"github_token\" to gateway_config.json to make this automatic.")
        return 2

    import base64
    api = "https://api.github.com/repos/%s/contents/%s" % (repo, path)
    hdr = {"Authorization": "Bearer " + token,
           "Accept": "application/vnd.github+json",
           "User-Agent": "nanobanana-gateway"}
    sha = None
    try:
        req = urllib.request.Request(api, headers=hdr)
        with urllib.request.urlopen(req, timeout=30) as r:
            sha = json.load(r).get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print("[ERROR] could not read the current file: HTTP %d" % e.code)
            return 1
    except Exception as e:
        print("[ERROR] GitHub unreachable: %s" % str(e)[:120])
        return 1

    payload = {"message": "gateway address -> %s" % address,
               "content": base64.b64encode(body.encode()).decode()}
    if sha:
        payload["sha"] = sha
    try:
        req = urllib.request.Request(api, data=json.dumps(payload).encode(),
                                     headers={**hdr, "Content-Type": "application/json"},
                                     method="PUT")
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
    except urllib.error.HTTPError as e:
        print("[ERROR] publish failed: HTTP %d %s" % (e.code, e.read()[:200].decode("utf-8","replace")))
        return 1
    except Exception as e:
        print("[ERROR] publish failed: %s" % str(e)[:120])
        return 1
    print("")
    print("  PUBLISHED: %s" % address)
    print("  Every app picks this up on its next launch. Nobody has to do anything.")
    return 0


if __name__ == "__main__":
    if "--address" in sys.argv:
        _safe_stdout()
        a, k = _detect_address()
        print(a or "(none)")
        raise SystemExit(0)
    if "--publish" in sys.argv:
        i = sys.argv.index("--publish")
        arg = sys.argv[i+1] if len(sys.argv) > i+1 and not sys.argv[i+1].startswith("-") else ""
        raise SystemExit(_publish(arg))
    if "--info" in sys.argv:
        _print_info()
        raise SystemExit(0)
    _load()
    port = int(os.environ.get("NB_GW_PORT", "8787"))
    host = os.environ.get("NB_GW_HOST", "127.0.0.1")
    print("[%s] listening on %s:%d" % (APP_NAME, host, port))
    print("[%s] config file  : %s" % (APP_NAME, _CONFIG_PATH if _CONFIG_FOUND else "(none, using env)"))
    print("[%s] data dir     : %s" % (APP_NAME, DATA_DIR))
    print("[%s] upstream     : %s" % (APP_NAME, UPSTREAM))
    print("[%s] upstream key : %s" % (APP_NAME, "loaded" if os.environ.get("NB_GW_OPENAI_KEY") else "MISSING"))
    print("[%s] tickets      : %d hash(es)" % (APP_NAME, len(_ticket_hashes())))
    print("[%s] enrollment   : %s" % (APP_NAME, "OPEN" if _state["enroll_open"] else "closed"))
    app.run(host=host, port=port, threaded=True)
