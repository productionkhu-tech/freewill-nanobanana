"""Client side of the NanoBanana key gateway.

The machine never carries a provider key. It carries a personal token (출입증)
and, at every start, shows that token to the gateway and gets every provider
key back. The keys live in memory; one encrypted copy (DPAPI on Windows, the
login Keychain on macOS) lets the app start while the gateway is unreachable.
Generation still goes straight to the providers — the gateway hands out keys,
it does not relay images, so nothing about generation speed changes.

Before 2026-10 the keys sat in plain text in user environment variables (and a
service-account key file for Vertex), put there by installer .bat files:
`echo %OPENAI_API_KEY%` showed them and every program the user launched
inherited them. `scrub_plaintext()` erases those copies once the gateway has
proven it can supply the same keys.

A new machine gets its token by admin approval: the app posts a request, shows
a 4-digit number, and the admin types that number into the admin page. The
older ticket enrolment (an OpenAI key we once handed out, as proof) still works
while the gateway keeps ENROLL_OPEN=1, so machines never enrolled carry on.

Kept out of app.py because launcher.py imports app.py in the frozen build, and
this module has to stay importable from the gateway tests too.
"""
import base64
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

TOKEN_FILENAME = "gateway.json"

# Where the gateway is, looked up at launch instead of compiled in.
#
# Baking an address into the build means a new office, a new router, or a DHCP
# lease change breaks every machine at once and can only be fixed by shipping a
# new release to 70 people. This file holds one line — the current address — so
# moving the gateway is a one-line edit that everyone picks up on next launch.
# It is public, which is fine: reaching the gateway still needs a token.
ENDPOINT_SOURCE = ("https://raw.githubusercontent.com/productionkhu-tech/"
                   "freewill-nanobanana/main/gateway_endpoint.txt")
ENDPOINT_CACHE = "gateway_endpoint.txt"

# Last-resort address compiled in. Empty = behave exactly like the old build and
# talk to the provider directly.
DEFAULT_GATEWAY_URL = ""

# Resolved address, reused for a while. Every key/token/catalog call used to
# re-read the address file from GitHub first (6s timeout) — fine once at start,
# wasteful on the hourly Vertex token refresh that sits in a generation's path.
_URL_MEMO = {}
_URL_TTL = 600


def _valid_url(u):
    u = (u or "").strip().rstrip("/")
    return u if u.startswith(("http://", "https://")) and len(u) < 300 else ""


def _fetch_endpoint(timeout=6):
    """Read the current address. Kept short: a slow network must not hold up
    startup, and a failure just falls through to the cached value."""
    try:
        req = urllib.request.Request(ENDPOINT_SOURCE,
                                     headers={"Cache-Control": "no-cache",
                                              "User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(4096).decode("utf-8", "replace")
    except Exception:
        return ""
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        return _valid_url(line)
    return ""


def gateway_url(data_dir=None):
    """Address for this run.

    Order: environment variable (a machine pointed somewhere on purpose), then
    the published address, then the last one that worked, then the compiled-in
    default. The cache is what keeps the app working when GitHub is unreachable
    but the gateway is perfectly fine."""
    env = _valid_url(os.environ.get("NANOBANANA_GATEWAY_URL", ""))
    if env:
        return env
    memo = _URL_MEMO.get(data_dir or "")
    if memo and memo[0] and time.time() - memo[1] < _URL_TTL:
        return memo[0]
    url = ""
    cache_path = os.path.join(data_dir, ENDPOINT_CACHE) if data_dir else None
    live = _fetch_endpoint()
    if live:
        if cache_path:
            try:
                os.makedirs(data_dir, exist_ok=True)
                with open(cache_path, "w", encoding="utf-8") as f:
                    f.write(live)
            except Exception:
                pass
        url = live
    elif cache_path and os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                url = _valid_url(f.read())
        except Exception:
            url = ""
    url = url or _valid_url(DEFAULT_GATEWAY_URL)
    if url:
        _URL_MEMO[data_dir or ""] = (url, time.time())
    return url


def ticket():
    """What the app offers in exchange for a token (ticket enrolment).

    NANOBANANA_TICKET wins so a machine can be pointed at the gateway without
    disturbing a working OPENAI_API_KEY; otherwise the OpenAI key an older
    installer left on the machine IS the ticket. Once those plaintext copies are
    erased there is no ticket any more, and a new machine goes through admin
    approval instead — which is the point."""
    raw = (os.environ.get("NANOBANANA_TICKET", "")
           or os.environ.get("OPENAI_API_KEY", "")) or ""
    # A BOM or stray whitespace from a paste hashes to something else on the
    # server, and the only symptom would be a 403 that looks like a bad ticket.
    return raw.replace("﻿", "").strip()


# Cloudflare turns away the default urllib agent as a bot, and the whole
# exchange would fail with a 403 that looks nothing like a real problem.
USER_AGENT = "NanoBanana/1.0"


def _post(url, obj, timeout=20, headers=None):
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": USER_AGENT,
                                          **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")


def _http_error_text(e):
    try:
        d = json.loads(e.read().decode("utf-8", "replace") or "{}")
    except Exception:
        d = {}
    return str(d.get("error") or ("HTTP %d" % e.code))


def _who(app_version=""):
    return {
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "unknown",
        "machine": platform.node() or "unknown",
        "app_version": app_version or os.environ.get("NANOBANANA_APP_VERSION", ""),
    }


# ---------------------------------------------------------------------- vault
# The token and the key copy are never written in plain text.
#   Windows : DPAPI. Only this Windows account on this PC can open it — copy the
#             file to another PC or another account and it does not decrypt.
#   macOS   : the login Keychain (via the `security` tool).
#   other   : nothing is stored; the keys come from the gateway on every start.
# A program running as the same user can still open it. What this stops is the
# casual kind of leak: reading it off the screen, copying a file, a backup.
VAULT_SUFFIX = ".vault"
_VAULT_ENTROPY = b"NanoBanana/gateway/v1"
_KEYCHAIN_SERVICE = "NanoBanana"
_vault_lock = threading.Lock()


def _dpapi(data, protect):
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(BLOB), ctypes.c_void_p, ctypes.POINTER(BLOB),
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(BLOB)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    src_buf = ctypes.create_string_buffer(data, len(data))
    ent_buf = ctypes.create_string_buffer(_VAULT_ENTROPY, len(_VAULT_ENTROPY))
    src = BLOB(len(data), ctypes.cast(src_buf, ctypes.POINTER(ctypes.c_char)))
    ent = BLOB(len(_VAULT_ENTROPY), ctypes.cast(ent_buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if not fn(ctypes.byref(src), None, ctypes.byref(ent), None, None,
              CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out)):
        raise OSError("DPAPI error %d" % ctypes.get_last_error())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))


def _kc_account(data_dir, name):
    # Per data folder, so a test run with its own data folder never touches the
    # real entries.
    tag = hashlib.sha256(os.path.abspath(data_dir).encode("utf-8")).hexdigest()[:8]
    return "%s@%s" % (name, tag)


def _vault_file(data_dir, name):
    return os.path.join(data_dir, name + VAULT_SUFFIX)


def vault_put(data_dir, name, obj):
    """Store a JSON-able value for this PC and this user. True when stored."""
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        with _vault_lock:
            if sys.platform == "win32":
                blob = _dpapi(raw, True)
                os.makedirs(data_dir, exist_ok=True)
                p = _vault_file(data_dir, name)
                tmp = p + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(blob)
                os.replace(tmp, p)
                return True
            if sys.platform == "darwin":
                r = subprocess.run(
                    ["security", "add-generic-password", "-U", "-s", _KEYCHAIN_SERVICE,
                     "-a", _kc_account(data_dir, name),
                     "-w", base64.b64encode(raw).decode("ascii")],
                    capture_output=True, timeout=15)
                return r.returncode == 0
    except Exception:
        return False
    return False


def vault_get(data_dir, name):
    """The stored value, or None (missing, or not openable by this account)."""
    try:
        with _vault_lock:
            if sys.platform == "win32":
                p = _vault_file(data_dir, name)
                if not os.path.isfile(p):
                    return None
                with open(p, "rb") as f:
                    blob = f.read()
                return json.loads(_dpapi(blob, False).decode("utf-8"))
            if sys.platform == "darwin":
                r = subprocess.run(
                    ["security", "find-generic-password", "-s", _KEYCHAIN_SERVICE,
                     "-a", _kc_account(data_dir, name), "-w"],
                    capture_output=True, text=True, timeout=15)
                if r.returncode != 0 or not r.stdout.strip():
                    return None
                return json.loads(base64.b64decode(r.stdout.strip()).decode("utf-8"))
    except Exception:
        return None
    return None


def vault_del(data_dir, name):
    try:
        with _vault_lock:
            if sys.platform == "win32":
                p = _vault_file(data_dir, name)
                if os.path.isfile(p):
                    os.remove(p)
            elif sys.platform == "darwin":
                subprocess.run(["security", "delete-generic-password", "-s", _KEYCHAIN_SERVICE,
                                "-a", _kc_account(data_dir, name)],
                               capture_output=True, timeout=15)
    except Exception:
        pass


# ---------------------------------------------------------------------- token
# gateway.json keeps who/when (readable, harmless); the token itself is in the
# vault. Older builds kept the token in gateway.json — it is moved on first read.
TOKEN_VAULT = "gw_token"
_token_lock = threading.Lock()


def _token_path(data_dir):
    return os.path.join(data_dir, TOKEN_FILENAME)


def _read_meta(data_dir):
    try:
        with open(_token_path(data_dir), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _write_meta(data_dir, meta):
    os.makedirs(data_dir, exist_ok=True)
    p = _token_path(data_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def load_token(data_dir, url):
    """Token for this gateway, or None. A token issued by a different gateway is
    ignored rather than sent somewhere it means nothing."""
    with _token_lock:
        meta = _read_meta(data_dir) or {}
        plain = meta.get("token") or ""
        if plain.startswith("nbt_"):
            if (meta.get("url") or "").rstrip("/") != url:
                return None
            # Left in plain text by an older build. Into the vault with it, and the
            # plain copy goes only once the vault has handed it back intact.
            rec = {"url": url, "token": plain, "token_id": meta.get("token_id", ""),
                   "since": time.time() - 86400}
            if vault_put(data_dir, TOKEN_VAULT, rec) and \
                    (vault_get(data_dir, TOKEN_VAULT) or {}).get("token") == plain:
                meta.pop("token", None)
                meta["token_in_vault"] = True
                try:
                    _write_meta(data_dir, meta)
                except Exception:
                    pass
            return plain
        v = vault_get(data_dir, TOKEN_VAULT) or {}
        tok = v.get("token") or ""
        if tok.startswith("nbt_") and (v.get("url") or "").rstrip("/") == url:
            return tok
        return None


def token_age(data_dir):
    """Seconds since this PC got its token (large when unknown)."""
    v = vault_get(data_dir, TOKEN_VAULT) or {}
    try:
        return max(0.0, time.time() - float(v.get("since") or 0))
    except Exception:
        return 1e9


def _save_token(data_dir, url, payload):
    tok = payload.get("token") or ""
    meta = {k: v for k, v in payload.items() if k != "token"}
    meta["url"] = url
    with _token_lock:
        rec = {"url": url, "token": tok, "token_id": payload.get("token_id", ""),
               "since": time.time()}
        if vault_put(data_dir, TOKEN_VAULT, rec) and \
                (vault_get(data_dir, TOKEN_VAULT) or {}).get("token") == tok:
            meta["token_in_vault"] = True
        else:
            meta["token"] = tok          # no vault on this platform: as before
        _write_meta(data_dir, meta)


def forget_token(data_dir):
    """Drop a token the gateway no longer honours (the admin cut this PC off)."""
    with _token_lock:
        vault_del(data_dir, TOKEN_VAULT)
        # Written even when gateway.json is missing: the mark is what keeps the next
        # start from falling back to plaintext keys (was_cut_off).
        meta = _read_meta(data_dir) or {}
        meta.pop("token", None)
        meta.pop("token_in_vault", None)
        meta["dropped_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            _write_meta(data_dir, meta)
        except Exception:
            pass


def was_cut_off(data_dir):
    """True after forget_token() until a new token is saved — so a PC the admin cut
    off does not carry on with plaintext keys an older installer left behind."""
    meta = _read_meta(data_dir) or {}
    return bool(meta.get("dropped_at")) and not meta.get("token")


def enroll(data_dir, url, app_version="", log=None):
    """Ticket enrolment: trade the ticket for a personal token and keep it.

    Returns (token, message). token is None when enrolment did not happen; the
    message is for the log, never for a dialog."""
    def _say(m):
        if log:
            log(m)

    tk = ticket()
    if not tk:
        return None, "gateway: no ticket on this machine"    # WHY_NO_TICKET
    body = dict(_who(app_version), ticket=tk)
    try:
        status, d = _post(url + "/enroll", body)
    except urllib.error.HTTPError as e:
        return None, "gateway: enrollment refused (%s)" % _http_error_text(e)
    except Exception as e:
        return None, "gateway: unreachable (%s)" % str(e)[:80]

    tok = d.get("token") or ""
    if status != 200 or not tok:
        return None, "gateway: enrollment failed (%s)" % (d.get("error") or status)
    _save_token(data_dir, url, {
        "url": url,
        "token": tok,
        "token_id": d.get("token_id", ""),
        "user": body["user"],
        "machine": body["machine"],
        "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "via": "ticket",
    })
    _say("gateway: enrolled as %s (%s)" % (d.get("token_id", "?"),
                                           "reused" if d.get("reused") else "new"))
    return tok, "ok"


# 등록이 왜 안 됐는지는 화면까지 가야 한다. 예전엔 로그에만 남겨서, 쓰는 사람은
# "등록되지 않았습니다" 만 보고 무엇을 해야 할지 알 수 없었다 (2026-09-17 현장 제보).
_LAST_TOKEN_REASON = {"msg": ""}


def token_failure_reason():
    """마지막 토큰 획득 실패를 사용자가 읽을 말로. 없으면 빈 문자열."""
    return _LAST_TOKEN_REASON["msg"]


def _humanize_enroll(msg):
    m = msg or ""
    if "no ticket" in m or "ticket not accepted" in m or "closed" in m or "cut off" in m:
        return "이 PC는 아직 승인되지 않았습니다 — 앱 화면의 번호를 관리자에게 알려 주세요"
    if "unreachable" in m:
        return "등록 서버에 연결하지 못했습니다 — 잠시 뒤 다시 시도해 주세요"
    if "too many attempts" in m or "429" in m:
        return "등록 시도가 너무 잦아 잠시 막혔습니다 — 10분 뒤 다시 시도해 주세요"
    return "등록하지 못했습니다 (%s)" % m.replace("gateway: ", "")[:80]


def get_token(data_dir, app_version="", log=None):
    """Token to use for this run, or None.

    Cached first: enrolment is a one-time event, and a gateway that is briefly
    down must not stop an app that already has its token."""
    url = gateway_url(data_dir)
    if not url:
        _LAST_TOKEN_REASON["msg"] = "게이트웨이 주소를 찾지 못했습니다"
        return None, ""
    tok = load_token(data_dir, url)
    if tok:
        _LAST_TOKEN_REASON["msg"] = ""
        return tok, url
    tok, msg = enroll(data_dir, url, app_version=app_version, log=log)
    if tok:
        _LAST_TOKEN_REASON["msg"] = ""
        return tok, url
    _LAST_TOKEN_REASON["msg"] = _humanize_enroll(msg)
    if log:
        log(msg)
    return None, url


# ----------------------------------------------------------------------- keys
KEYS_VAULT = "gw_keys"


class GatewayError(Exception):
    """kind: no_url | no_token | revoked | invalid | unreachable | refused"""

    def __init__(self, kind, message=""):
        super().__init__(message or kind)
        self.kind = kind


def _bearer(data_dir):
    url = gateway_url(data_dir)
    if not url:
        raise GatewayError("no_url", "gateway address not found")
    token = load_token(data_dir, url)
    if not token:
        raise GatewayError("no_token", "this PC is not enrolled")
    return url, {"Authorization": "Bearer " + token}


def fetch_keys(data_dir, app_version="", timeout=15):
    """Every provider key, from the gateway. Kept in the vault as the offline copy.

    Returns a dict: openai/studio/ark (str), vertex (dict or None), known_hashes,
    key_ids, fetched_at. Raises GatewayError."""
    url, auth = _bearer(data_dir)
    try:
        status, d = _post(url + "/key", dict(_who(app_version), v=2),
                          timeout=timeout, headers=auth)
    except urllib.error.HTTPError as e:
        msg = _http_error_text(e)
        if e.code == 401:
            raise GatewayError("revoked" if "revoked" in msg else "invalid", msg)
        raise GatewayError("refused", msg)
    except Exception as e:
        raise GatewayError("unreachable", str(e)[:80])
    if status != 200 or not d.get("ok") or not isinstance(d.get("keys"), dict):
        raise GatewayError("refused", str(d.get("error") or status))
    k = d["keys"]
    keys = {
        "openai": str(k.get("openai") or "").strip(),
        "studio": str(k.get("studio") or "").strip(),
        "ark": str(k.get("ark") or "").strip(),
        "vertex": None,
        "known_hashes": [h for h in (d.get("known_hashes") or []) if isinstance(h, str)],
        "key_ids": d.get("key_ids") or {},
        "fetched_at": time.time(),
    }
    vx = d.get("vertex")
    if isinstance(vx, dict) and vx.get("project"):
        exp_in = float(vx.get("expires_in") or 0) if vx.get("token") else 0.0
        keys["vertex"] = {
            "project": vx.get("project"),
            "location": vx.get("location") or "global",
            "sa_email": vx.get("sa_email") or "",
            "token": vx.get("token") or "",
            "expires_at": time.time() + exp_in if exp_in > 0 else 0.0,
        }
    vault_put(data_dir, KEYS_VAULT, keys)
    return keys


def cached_keys(data_dir):
    """The encrypted copy from the last successful fetch, or None."""
    v = vault_get(data_dir, KEYS_VAULT)
    if isinstance(v, dict) and (v.get("openai") or v.get("studio") or v.get("ark")
                                or v.get("vertex")):
        return v
    return None


def forget_keys(data_dir):
    vault_del(data_dir, KEYS_VAULT)


def vertex_token(data_dir, timeout=15):
    """(access_token, expires_in_seconds) for Vertex. Raises GatewayError."""
    url, auth = _bearer(data_dir)
    try:
        status, d = _post(url + "/vertex-token", {}, timeout=timeout, headers=auth)
    except urllib.error.HTTPError as e:
        msg = _http_error_text(e)
        if e.code == 401:
            raise GatewayError("revoked" if "revoked" in msg else "invalid", msg)
        raise GatewayError("refused", msg)
    except Exception as e:
        raise GatewayError("unreachable", str(e)[:80])
    if not d.get("ok") or not d.get("token"):
        raise GatewayError("refused", str(d.get("error") or status))
    return d["token"], float(d.get("expires_in") or 0)


# --------------------------------------------------------- approval enrolment
ENROLL_VAULT = "gw_enroll"
_ENROLL_MEMO = {}
ENROLL_TTL = 3300        # the gateway keeps a request for an hour; renew a bit early


def pending_enrollment(data_dir):
    rec = _ENROLL_MEMO.get(data_dir) or vault_get(data_dir, ENROLL_VAULT) or {}
    if rec.get("req_id") and time.time() - float(rec.get("created") or 0) < ENROLL_TTL:
        return rec
    return None


def clear_enrollment(data_dir):
    _ENROLL_MEMO.pop(data_dir, None)
    vault_del(data_dir, ENROLL_VAULT)


def request_enrollment(data_dir, app_version=""):
    """Ask the admin to let this PC in. Reuses this PC's own open request (so a
    restart keeps the same number). Returns {"code", "req_id", ...}.
    Raises GatewayError."""
    url = gateway_url(data_dir)
    if not url:
        raise GatewayError("no_url", "gateway address not found")
    cur = pending_enrollment(data_dir)
    if cur and cur.get("url") == url:
        return cur
    try:
        status, d = _post(url + "/enroll/request", _who(app_version), timeout=20)
    except urllib.error.HTTPError as e:
        raise GatewayError("refused", _http_error_text(e))
    except Exception as e:
        raise GatewayError("unreachable", str(e)[:80])
    if status != 200 or not d.get("ok") or not d.get("req_id"):
        raise GatewayError("refused", str(d.get("error") or status))
    rec = {"url": url, "req_id": d["req_id"], "code": str(d.get("code") or ""),
           "created": time.time()}
    _ENROLL_MEMO[data_dir] = rec
    vault_put(data_dir, ENROLL_VAULT, rec)
    return rec


def poll_enrollment(data_dir, app_version=""):
    """'approved' (token now saved) | 'pending' | 'denied' | 'expired' | 'none'.
    Raises GatewayError on network trouble."""
    rec = pending_enrollment(data_dir)
    if not rec:
        return "none"
    try:
        status, d = _post(rec["url"] + "/enroll/poll", {"req_id": rec["req_id"]}, timeout=15)
    except urllib.error.HTTPError as e:
        raise GatewayError("refused", _http_error_text(e))
    except Exception as e:
        raise GatewayError("unreachable", str(e)[:80])
    st = d.get("status") or "unknown"
    tok = str(d.get("token") or "")
    if st == "approved" and tok.startswith("nbt_"):
        who = _who(app_version)
        _save_token(data_dir, rec["url"], {
            "url": rec["url"], "token": tok, "token_id": d.get("token_id", ""),
            "user": who["user"], "machine": who["machine"],
            "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "via": "approval",
        })
        clear_enrollment(data_dir)
        return "approved"
    if st == "pending":
        return "pending"
    # denied / expired / unknown / picked_up (handed out but lost before saving):
    # the request is over either way; a new one gets a new number.
    clear_enrollment(data_dir)
    return "denied" if st == "denied" else "expired"


# ---------------------------------------------------------- plaintext cleanup
# The copies older installers left behind. Generic names (OPENAI_API_KEY,
# ARK_API_KEY, REVE_API_KEY) are only removed when the value is one of OUR keys
# by hash — another program on this PC may use the same name with its own key.
# NANOBANANA_* names are ours by definition.
PLACEHOLDER = "managed-by-gateway"
_KEY_SHAPES = (r"sk-[A-Za-z0-9_\-]{20,}", r"AIza[0-9A-Za-z_\-]{30,}",
               r"ark-[0-9a-f\-]{20,}", r"papi\.[A-Za-z0-9_\-\.]{10,}")


def _h(v):
    return hashlib.sha256((v or "").replace("﻿", "").strip().encode("utf-8")).hexdigest()


def is_placeholder(v):
    return (v or "").strip() == PLACEHOLDER


def _sa_email_of(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("client_email") or ""
    except Exception:
        return ""


def _remove_our_sa_file(path, sa_email, done):
    """Delete a Vertex key file only when it is the gateway's own service account."""
    try:
        if path and sa_email and os.path.isfile(path) and _sa_email_of(path) == sa_email:
            os.remove(path)
            done.append("file:" + os.path.basename(path))
    except Exception:
        pass


def _broadcast_env_change():
    """Tell Explorer the user environment changed, so programs started from now
    on stop inheriting the removed values (same thing setx does)."""
    try:
        import ctypes
        res = ctypes.c_size_t(0)
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, ctypes.c_wchar_p("Environment"), 0x0002, 3000, ctypes.byref(res))
    except Exception:
        pass


def scrub_plaintext(data_dir, keys, defaults=None, log=None, _env_key="Environment", _home=None):
    """Erase the plaintext key copies older installers left on this machine.

    Only what the gateway now supplies is erased, and only what is provably ours.
    NANOBANANA_STUDIO_KEY is replaced by a placeholder instead of deleted on
    Windows: older builds refuse to start without it, and an old copy of the EXE
    has to boot far enough to update itself.

    `keys` must come from a fresh gateway fetch — never from the offline copy.
    Returns short ASCII labels of what was removed. (_env_key/_home exist for
    tests, which must never touch the real environment or key file.)"""
    known = set(keys.get("known_hashes") or [])
    for k in ("openai", "studio", "ark"):
        if keys.get(k):
            known.add(_h(keys[k]))
    vx = keys.get("vertex") or {}
    sa_email = vx.get("sa_email") or ""
    defaults = {k.upper(): v for k, v in (defaults or {}).items()}
    home = _home or os.path.expanduser("~")
    done = []

    def decide(name, value):
        n, v = name.upper(), (value or "").strip()
        if not v:
            return None
        if n == "NANOBANANA_STUDIO_KEY":
            return "placeholder" if keys.get("studio") and not is_placeholder(v) else None
        if n in ("NANOBANANA_REAL_KEY_BACKUP", "NANOBANANA_TICKET"):
            return "drop" if keys.get("openai") else None
        if n == "OPENAI_API_KEY":
            return "drop" if keys.get("openai") and _h(v) in known else None
        if n == "ARK_API_KEY":
            return "drop" if keys.get("ark") and _h(v) in known else None
        if n == "REVE_API_KEY":
            return "drop" if _h(v) in known else None
        if n in ("NANOBANANA_PROJECT_ID", "NANOBANANA_LOCATION"):
            return "drop" if vx.get("project") else None
        if n == "GOOGLE_APPLICATION_CREDENTIALS":
            if not sa_email:
                return None
            p = os.path.expandvars(v)
            home_nb = os.path.join(home, ".nanobanana")
            if os.path.isfile(p):
                return "drop" if _sa_email_of(p) == sa_email else None
            # Points at a file that is already gone: ours only if it was in our folder.
            return "drop" if os.path.normcase(os.path.dirname(os.path.abspath(p))) == \
                os.path.normcase(home_nb) else None
        if n in defaults:
            return "drop" if v == defaults[n] else None
        return None

    if sys.platform == "win32":
        try:
            import winreg
            cur = {}
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _env_key) as k:
                i = 0
                while True:
                    try:
                        n, v, _t = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    cur[n] = v if isinstance(v, str) else ""
                    i += 1
            changed = False
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _env_key, 0,
                                winreg.KEY_SET_VALUE) as k:
                for n, v in cur.items():
                    act = decide(n, v)
                    if act is None:
                        continue
                    if n.upper() == "GOOGLE_APPLICATION_CREDENTIALS":
                        _remove_our_sa_file(os.path.expandvars(v), sa_email, done)
                    if act == "placeholder":
                        winreg.SetValueEx(k, n, 0, winreg.REG_SZ, PLACEHOLDER)
                        os.environ[n] = PLACEHOLDER
                    else:
                        winreg.DeleteValue(k, n)
                        os.environ.pop(n, None)
                    done.append(n.upper())
                    changed = True
                # The placeholder has to be there whenever we removed anything, not only
                # when a Studio key used to exist: a PC that had only the Vertex variables
                # would otherwise leave an old EXE nothing to start with.
                studio_was = next((v for n, v in cur.items()
                                   if n.upper() == "NANOBANANA_STUDIO_KEY"), "")
                if changed and not (studio_was or "").strip():
                    winreg.SetValueEx(k, "NANOBANANA_STUDIO_KEY", 0, winreg.REG_SZ, PLACEHOLDER)
                    os.environ["NANOBANANA_STUDIO_KEY"] = PLACEHOLDER
            if changed and _env_key == "Environment":
                _broadcast_env_change()
        except Exception as e:
            if log:
                log("plaintext cleanup: environment step failed (%s)" % str(e)[:80])
        if _env_key == "Environment":
            done.extend(_scrub_installer_bats(known))
    elif sys.platform == "darwin":
        here = os.path.dirname(os.path.abspath(__file__))
        for path in (os.path.join(here, "keys.env"), os.path.join(home, ".nanobanana", "keys.env")):
            try:
                _scrub_keys_env(path, decide, sa_email, done)
            except Exception as e:
                if log:
                    log("plaintext cleanup: keys.env step failed (%s)" % str(e)[:80])
    # The key file at its usual place goes too, even when no variable points at it.
    _remove_our_sa_file(os.path.join(home, ".nanobanana", "service_account.json"),
                        sa_email, done)
    return sorted(set(done))


def _scrub_keys_env(path, decide, sa_email, done):
    """macOS: drop our lines from a keys.env (the Mac installer's plaintext file).
    No placeholder is needed there — the Mac build always runs current code."""
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    keep, changed = [], False
    for ln in lines:
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", ln)
        if m and not ln.lstrip().startswith("#"):
            n, v = m.group(1), m.group(2).strip().strip('"').strip("'")
            if n.upper() == "GOOGLE_APPLICATION_CREDENTIALS" and v and not os.path.isabs(v):
                v = os.path.join(os.path.dirname(path), v)
            if decide(n, v):
                if n.upper() == "GOOGLE_APPLICATION_CREDENTIALS":
                    _remove_our_sa_file(v, sa_email, done)
                os.environ.pop(n, None)
                done.append(n.upper())
                changed = True
                continue
        keep.append(ln)
    if changed:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(keep) + ("\n" if keep else ""))
        os.replace(tmp, path)


def _scrub_installer_bats(known):
    """Installer .bat files next to the EXE that carry one of our keys.

    We shipped them side by side with the EXE, each with a key written inside
    in plain text. Only files that contain one of our keys (by hash) and set
    variables are removed — nothing else in the folder is touched."""
    if not getattr(sys, "frozen", False):
        return []
    removed = []
    folder = os.path.dirname(os.path.abspath(sys.executable))
    try:
        names = os.listdir(folder)
    except Exception:
        return []
    for name in names:
        if not name.lower().endswith(".bat"):
            continue
        p = os.path.join(folder, name)
        try:
            if os.path.getsize(p) > 256 * 1024:
                continue
            with open(p, "rb") as f:
                txt = f.read().decode("utf-8", "replace")
        except Exception:
            continue
        if "setx" not in txt.lower():
            continue
        hits = [m for pat in _KEY_SHAPES for m in re.findall(pat, txt)]
        if not any(_h(m) in known for m in hits):
            continue
        try:
            os.remove(p)
            removed.append("bat")
        except Exception:
            pass
    return ["installer-bat x%d" % len(removed)] if removed else []


# ==========================================================================
# 사용량 집계 (팀/프로젝트별 비용)
# ==========================================================================
# 게이트웨이는 키만 나눠주고 생성은 앱이 직접 한다(속도 때문에 그렇게 정했다).
# 그래서 워커는 생성 트래픽을 볼 수 없고, 사용량은 앱이 사후 보고해야 한다.
#
# 보고가 생성을 방해하면 안 된다는 게 이 구현의 전부다: 생성 직후에는 로컬
# 파일에 한 줄 덧붙이기만 하고(마이크로초), 전송은 데몬 스레드가 모아서 한다.
# 네트워크가 죽어 있으면 줄이 쌓인 채로 남았다가 다음에 올라간다.

SPOOL_FILENAME = "usage_spool.jsonl"
_spool_lock = threading.Lock()


def _spool_path(data_dir):
    return os.path.join(data_dir, SPOOL_FILENAME)


def spool_usage(data_dir, event):
    """사용량 한 건을 로컬에 적어둔다. 실패해도 절대 위로 던지지 않는다 —
    집계가 안 되는 것보다 생성이 막히는 게 훨씬 나쁘다."""
    try:
        os.makedirs(data_dir, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False)
        with _spool_lock:
            with open(_spool_path(data_dir), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return True
    except Exception:
        return False


def _read_spool(data_dir, limit=200):
    try:
        with _spool_lock:
            with open(_spool_path(data_dir), "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
    except Exception:
        return [], 0
    events = []
    for ln in lines[:limit]:
        try:
            events.append(json.loads(ln))
        except Exception:
            pass          # 깨진 줄은 버린다 (아래에서 같이 소비 처리된다)
    return events, min(len(lines), limit)


def _drop_spool_head(data_dir, n):
    """전송에 성공한 앞쪽 n줄을 덜어낸다. 원자적으로 교체해서, 도중에 죽어도
    파일이 반쯤 잘린 상태로 남지 않게 한다."""
    p = _spool_path(data_dir)
    try:
        with _spool_lock:
            with open(p, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
            rest = lines[n:]
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                if rest:
                    f.write("\n".join(rest) + "\n")
            os.replace(tmp, p)
        return True
    except Exception:
        return False


def fetch_catalog(data_dir, app_version="", log=None):
    """팀/프로젝트 목록. 서버에서 바꾸면 다음 조회에 바로 반영된다.
    Returns (catalog_dict, error_message)."""
    url = gateway_url(data_dir)
    if not url:
        return None, "게이트웨이 주소를 찾지 못했습니다"
    token, _ = get_token(data_dir, app_version=app_version, log=log)
    if not token:
        why = token_failure_reason()
        return None, why or "이 PC가 아직 게이트웨이에 등록되지 않았습니다"
    try:
        req = urllib.request.Request(
            url + "/catalog",
            headers={"User-Agent": USER_AGENT, "Authorization": "Bearer " + token})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        return None, "목록을 받지 못했습니다 (HTTP %d)" % e.code
    except Exception as e:
        return None, "목록 서버에 연결하지 못했습니다 (%s)" % str(e)[:60]
    if not d.get("ok"):
        return None, d.get("error") or "목록을 받지 못했습니다"
    return d, None


def flush_usage(data_dir, app_version="", log=None, batch=200):
    """쌓인 사용량을 한 번 올린다. 올린 만큼만 지운다.

    이벤트 id 는 앱이 만들고 서버가 INSERT OR IGNORE 로 받으므로, 전송은
    됐는데 응답을 못 받아 재전송하는 경우에도 중복이 쌓이지 않는다.
    Returns (sent_count, error_message_or_None)."""
    events, taken = _read_spool(data_dir, batch)
    if not taken:
        return 0, None
    url = gateway_url(data_dir)
    if not url:
        return 0, "no gateway"
    token = load_token(data_dir, url)
    if not token:
        return 0, "not enrolled"
    try:
        status, d = _post(url + "/usage", {"events": events}, timeout=25,
                          headers={"Authorization": "Bearer " + token})
    except urllib.error.HTTPError as e:
        # 끊긴 PC 는 여기서 가장 먼저 알게 된다 (생성할 때마다 올리므로). 앱이 키를 내려놓는다.
        if e.code == 401 and "revoked" in _http_error_text(e):
            return 0, "revoked"
        return 0, "HTTP %d" % e.code
    except Exception as e:
        return 0, str(e)[:60]
    if status != 200 or not d.get("ok"):
        return 0, str(d.get("error") or status)[:60]
    _drop_spool_head(data_dir, taken)
    return taken, None
