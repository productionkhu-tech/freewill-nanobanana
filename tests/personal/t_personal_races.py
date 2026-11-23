# -*- coding: utf-8 -*-
"""Logic review of the personal keys (2026-11-23, after Task 8):
1. two keys saved at the same moment (two windows / quick clicks): both must stay - each save used to copy
   the record, check over the network, and write its copy back, so the slower one wiped the other key;
2. a vault that cannot be opened yet must not hold the start (tab restore) for the 30 s of retries;
3. an OpenAI key restricted to images (403 on the model list) is a working key - not refused;
4. a key a provider turns down while generating shows as rejected on the screen at once (event).
usage: python tests/personal/t_personal_races.py"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _h import FakeHTTP, check, done, isolate  # noqa: E402

P = isolate("personal")


def slow_openai(req):
    time.sleep(1.0)
    a = req.headers.get("authorization")
    if a == "Bearer sk-good1234":
        return 200, {"data": []}
    if a == "Bearer sk-imagesonly1":
        return 403, {"error": {"message": "You have insufficient permissions for this operation. Missing scopes: api.model.read"}}
    return 401, {"error": {"message": "Incorrect API key"}}


def slow_gemini(req):
    time.sleep(0.3)
    return (200, {"models": []}) if req.headers.get("x-goog-api-key") == "AIzaMine12345678" else (400, {})


fake = FakeHTTP({("GET", "/v1/models"): slow_openai, ("GET", "/v1beta/models"): slow_gemini})
os.environ.update({"NANOBANANA_TEST_OPENAI_BASE": fake.base, "NANOBANANA_TEST_GEMINI_BASE": fake.base,
                   "NANOBANANA_GATEWAY_URL": fake.base, "OPENAI_BASE_URL": fake.base + "/v1"})
import app as A  # noqa: E402
import personal_keys as K  # noqa: E402

A.init_app()
H = {"X-NB-Token": A.CSRF_TOKEN}


def post(body, out=None):
    r = A.app.test_client().post("/api/personal/keys", json=body, headers=H).get_json()
    if out is not None:
        out.append(r)
    return r


def keys():
    return A.app.test_client().get("/api/personal/keys", headers=H).get_json()["keys"]


# 1. two saves at once: OpenAI (slow check, 1 s) and Gemini (fast, 0.3 s). The vault write is made slow
# (1.5 s) so the two saves overlap there - a slow disk / a busy DPAPI does that for real.
_real_save = K.save


def _slow_save(dd, rec):
    time.sleep(1.5)
    return _real_save(dd, rec)


K.save = _slow_save
res = []
t1 = threading.Thread(target=post, args=({"provider": "openai", "key": "sk-good1234"}, res))
t2 = threading.Thread(target=post, args=({"provider": "studio", "key": "AIzaMine12345678"}, res))
t1.start(); time.sleep(0.05); t2.start(); t1.join(); t2.join()
k = keys()
check("both answered ok", len(res) == 2 and all(r.get("ok") for r in res), res)
check("both keys kept", k["openai"]["set"] and k["studio"]["set"], {p: k[p]["set"] for p in k})
kind, rec = K.load(P["data"])
check("both keys in the vault", kind == "ok" and set(rec["keys"]) >= {"openai", "studio"}, sorted((rec or {}).get("keys", {})))
check("clients for both", A.shared.client_openai is not None and A.shared.client_studio is not None)
K.save = _real_save

# 3. an images-only OpenAI key
r = K.verify("openai", "sk-imagesonly1")
check("restricted OpenAI key is accepted", r["result"] == "ok", r)
check("a wrong OpenAI key is still refused", K.verify("openai", "sk-wrong9999")["result"] == "invalid")
check("restricted key saves", post({"provider": "openai", "key": "sk-imagesonly1"}).get("ok"))
A.state._openai_selftest()          # the start-up check lists models too: that 403 is not a broken key
check("restricted key: status dot not red", A.state.openai_status == "connected", (A.state.openai_status, A.state.openai_detail))
check("restore the good key", post({"provider": "openai", "key": "sk-good1234"}).get("ok"))

# 4. rejected while generating -> the screen hears it
c = A.app.test_client()
c.get("/api/events", headers=H)                        # drain
A._keys_changed_since(A._keys_rt["ids"], "OpenAI")
ev = c.get("/api/events", headers=H).get_json().get("events") or []
check("rejected key -> personal_keys event", any(e.get("type") == "personal_keys" for e in ev), ev)
check("...and shown rejected", keys()["openai"]["state"] == "rejected")

# 2. an unreadable vault does not hold the start
open(os.path.join(P["data"], K.VAULT_NAME + ".vault"), "wb").write(b"from another account")
A.PERSONAL_RETRY_S = 0.05                              # 60 tries -> 3 s
t0 = time.time()
A._init_personal_keys()
took = time.time() - t0
check("start not held by the retries", took < 1.0, round(took, 2))
check("loading meanwhile", A._personal["phase"] == "loading")
g = A.app.test_client().post("/api/generate", headers=H).get_json()
check("generate says loading meanwhile", "불러오는 중" in (g.get("error") or ""), g)
t0 = time.time()
while A._personal["phase"] == "loading" and time.time() - t0 < 10:
    time.sleep(0.1)
check("then unreadable", A._personal["phase"] == "unreadable")
done()
