"""Client side of the NanoBanana key gateway.

The app used to carry the provider key itself, which meant reinstalling on ~70
machines every time a key rotated. With a gateway the machine carries a personal
token instead, and the token is obtained automatically: the app presents the
TICKET everyone already has (the retired OpenAI key still in their environment)
and gets a token of its own back.

Kept out of app.py because launcher.py imports app.py in the frozen build, and
this module has to stay importable from the gateway tests too.
"""
import json
import os
import platform
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
# It is public, which is fine: reaching the gateway still needs a ticket to
# enroll and a token to generate.
ENDPOINT_SOURCE = ("https://raw.githubusercontent.com/productionkhu-tech/"
                   "freewill-nanobanana/main/gateway_endpoint.txt")
ENDPOINT_CACHE = "gateway_endpoint.txt"

# Last-resort address compiled in. Empty = behave exactly like the old build and
# talk to the provider directly.
DEFAULT_GATEWAY_URL = ""


def _valid_url(u):
    u = (u or "").strip().rstrip("/")
    return u if u.startswith(("http://", "https://")) and len(u) < 300 else ""


def _fetch_endpoint(timeout=6):
    """Read the current address. Kept short: a slow network must not hold up
    startup, and a failure just falls through to the cached value."""
    try:
        req = urllib.request.Request(ENDPOINT_SOURCE,
                                     headers={"Cache-Control": "no-cache"})
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
        return live
    if cache_path and os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = _valid_url(f.read())
            if cached:
                return cached
        except Exception:
            pass
    return _valid_url(DEFAULT_GATEWAY_URL)


def ticket():
    """What the app offers in exchange for a token.

    NANOBANANA_TICKET wins so a machine can be pointed at the gateway without
    disturbing a working OPENAI_API_KEY; otherwise the OpenAI key already on the
    machine IS the ticket, which is the whole point — nothing to distribute."""
    raw = (os.environ.get("NANOBANANA_TICKET", "")
           or os.environ.get("OPENAI_API_KEY", "")) or ""
    # A BOM or stray whitespace from a paste hashes to something else on the
    # server, and the only symptom would be a 403 that looks like a bad ticket.
    return raw.replace("﻿", "").strip()


def _token_path(data_dir):
    return os.path.join(data_dir, TOKEN_FILENAME)


def load_token(data_dir, url):
    """Cached token for this gateway, or None. A token issued by a different
    gateway is ignored rather than sent somewhere it means nothing."""
    try:
        with open(_token_path(data_dir), "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    if (d.get("url") or "").rstrip("/") != url:
        return None
    tok = d.get("token") or ""
    return tok if tok.startswith("nbt_") else None


def _save_token(data_dir, url, payload):
    os.makedirs(data_dir, exist_ok=True)
    p = _token_path(data_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def _post(url, obj, timeout=20):
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")


def enroll(data_dir, url, app_version="", log=None):
    """Trade the ticket for a personal token and cache it.

    Returns (token, message). token is None when enrolment did not happen; the
    message is for the log, never for a dialog — a machine that cannot enrol
    falls back to the direct key and keeps working."""
    def _say(m):
        if log:
            log(m)

    tk = ticket()
    if not tk:
        return None, "gateway: no ticket on this machine"
    body = {
        "ticket": tk,
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "unknown",
        "machine": platform.node() or "unknown",
        "app_version": app_version,
    }
    try:
        status, d = _post(url + "/enroll", body)
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode("utf-8", "replace") or "{}")
        except Exception:
            d = {}
        msg = d.get("error") or ("HTTP %d" % e.code)
        return None, "gateway: enrollment refused (%s)" % msg
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
    })
    _say("gateway: enrolled as %s (%s)" % (d.get("token_id", "?"),
                                           "reused" if d.get("reused") else "new"))
    return tok, "ok"


def get_token(data_dir, app_version="", log=None):
    """Token to use for this run, or None to fall back to the direct key.

    Cached first: enrolment is a one-time event, and a gateway that is briefly
    down must not stop an app that already has its token."""
    url = gateway_url(data_dir)
    if not url:
        return None, ""
    tok = load_token(data_dir, url)
    if tok:
        return tok, url
    tok, msg = enroll(data_dir, url, app_version=app_version, log=log)
    if tok:
        return tok, url
    if log:
        log(msg)
    return None, url
