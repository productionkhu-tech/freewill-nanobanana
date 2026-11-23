# -*- coding: utf-8 -*-
"""The personal edition's own keys (v2026-11-23 design, spec 5.3 / 5.4).

People put in their own Google AI Studio / OpenAI / BytePlus keys inside the app. Each key is tidied,
checked for free (nothing is generated), and kept in a DPAPI vault that only this Windows account on this
PC can open - never in plain text. The screen only ever gets the last four characters back.

The personal edition has nothing to do with the company (user 2026-11-23): no gateway, no company-key
check - a key goes only to its own provider.

No Flask, no app import: app.py wires this in (edition.PERSONAL only)."""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

import nb_gateway

PROVIDERS = ("studio", "openai", "ark")
VAULT_NAME = "personal_keys"
_DEFAULT_BASE = {
    "GEMINI": "https://generativelanguage.googleapis.com",
    "OPENAI": "https://api.openai.com",
    "ARK": "https://ark.ap-southeast.bytepluses.com/api/v3",
}
TIMEOUT_S = 15
_UA = "NanoBanana-Personal"


def _base(name):
    """Provider address. The NANOBANANA_TEST_*_BASE switches are for isolated tests and only work from
    source - an installed EXE always talks to the real address."""
    if not getattr(sys, "frozen", False):
        v = (os.environ.get("NANOBANANA_TEST_%s_BASE" % name) or "").strip().rstrip("/")
        if v:
            return v
    return _DEFAULT_BASE[name]


# ------------------------------------------------------------------ the vault
def load(data_dir):
    """('ok', rec) | ('missing', None) | ('unreadable', None) - a file that is there but cannot be opened
    (another account / PC, not ready yet right after logon, damaged) is NOT 'no keys'."""
    if not os.path.isfile(os.path.join(data_dir, VAULT_NAME + nb_gateway.VAULT_SUFFIX)):
        return "missing", None
    rec = nb_gateway.vault_get(data_dir, VAULT_NAME)
    if not isinstance(rec, dict) or not isinstance(rec.get("keys"), dict):
        return "unreadable", None
    return "ok", rec


def save(data_dir, rec):
    """True when stored. No plain-text fallback."""
    return bool(nb_gateway.vault_put(data_dir, VAULT_NAME, rec))


def empty():
    return {"v": 1, "keys": {}}


# ------------------------------------------------------------------ tidying
def clean_key(raw):
    """(key, None) or (None, 'bad_key'). Takes off what people paste around a key: spaces, line breaks,
    quotes, a 'Bearer ' prefix."""
    if not isinstance(raw, str):
        return None, "bad_key"
    k = raw.strip()
    for _ in range(2):
        if len(k) >= 2 and k[0] == k[-1] and k[0] in "\"'`":
            k = k[1:-1].strip()
    if k.lower().startswith("bearer "):
        k = k[7:].strip()
    if not (8 <= len(k) <= 400) or re.search(r"\s", k) or not all(33 <= ord(c) < 127 for c in k):
        return None, "bad_key"
    return k, None


# ------------------------------------------------------------------ talking to the outside
def _http(method, url, headers=None, body=None, timeout=TIMEOUT_S):
    """(status, parsed body or {}). Raises OSError for network trouble."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = {"User-Agent": _UA}
    if data is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    try:
        return status, json.loads(raw.decode("utf-8", "replace") or "{}")
    except Exception:
        return status, {}


def _reason(obj, key):
    """A short provider message for the screen, with the key itself never in it."""
    err = obj.get("error") if isinstance(obj, dict) else None
    msg = ""
    if isinstance(err, dict):
        msg = str(err.get("message") or err.get("code") or err.get("status") or "")
    elif err:
        msg = str(err)
    return msg.replace(key, "***")[:200] if key else msg[:200]


def verify(provider, key, ark_models=None, ark_base=None):
    """Check a key without generating anything. {'result': 'ok'|'invalid'|'net', 'reason', 'models_off'}.

    studio: the model list (key in a header, never in the address). openai: the model list (403 is a
    real key without the "list models" permission - accepted).
    ark: one request per Seedream model with a size BytePlus rejects before doing any work -
    400 = the model can be used, 403/404 = not activated on this account, 401 = wrong key."""
    out = {"result": "net", "reason": "", "models_off": []}
    try:
        if provider == "studio":
            st, obj = _http("GET", _base("GEMINI") + "/v1beta/models?pageSize=200", {"x-goog-api-key": key})
            out["result"] = "ok" if st == 200 else "invalid" if st in (400, 401, 403) else "net"
            out["reason"] = "" if st == 200 else _reason(obj, key) or ("HTTP %d" % st)
        elif provider == "openai":
            st, obj = _http("GET", _base("OPENAI") + "/v1/models", {"Authorization": "Bearer " + key})
            # 403 = the key is real but may not list models (a key restricted to images) - it works.
            out["result"] = "ok" if st in (200, 403) else "invalid" if st == 401 else "net"
            out["reason"] = "" if st == 200 else _reason(obj, key) or ("HTTP %d" % st)
        elif provider == "ark":
            base = (ark_base or _base("ARK")).rstrip("/")
            any_ok = any_net = False
            for app_id, wire in (ark_models or {}).items():
                st, obj = _http("POST", base + "/images/generations", {"Authorization": "Bearer " + key},
                                {"model": wire, "prompt": "x", "size": "1x1"})
                if st == 401:
                    return {"result": "invalid", "reason": _reason(obj, key) or "HTTP 401", "models_off": []}
                if st == 400:
                    any_ok = True
                elif st in (403, 404):
                    out["models_off"].append(app_id)
                else:
                    any_net = True
                    out["reason"] = _reason(obj, key) or ("HTTP %d" % st)
            out["result"] = "net" if (any_net and not any_ok) else "ok"
        else:
            return {"result": "invalid", "reason": "unknown provider", "models_off": []}
    except Exception as e:
        out["result"], out["reason"] = "net", type(e).__name__
    return out


# ------------------------------------------------------------------ what the screen and the clients get
def public_status(rec):
    keys = (rec or {}).get("keys") or {}
    out = {}
    for p in PROVIDERS:
        k = keys.get(p) or {}
        out[p] = {"set": bool(k.get("key")), "tail": (k.get("key") or "")[-4:], "state": k.get("state") or "",
                  "models_off": list(k.get("models_off") or [])}
    return out


def client_keys(rec):
    keys = (rec or {}).get("keys") or {}
    return {"studio": (keys.get("studio") or {}).get("key") or "",
            "openai": (keys.get("openai") or {}).get("key") or "",
            "ark": (keys.get("ark") or {}).get("key") or "",
            "vertex": None}


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
