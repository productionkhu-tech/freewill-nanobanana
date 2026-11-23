# -*- coding: utf-8 -*-
"""Task 4: personal_keys - tidy a pasted key, check it for free with its own provider, store it with DPAPI
(spec 5.3, 5.4). No company-key check: the personal edition has nothing to do with the company (2026-11-23).
usage: python tests/personal/t_personal_keys.py"""
import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _h import FakeHTTP, check, done, isolate  # noqa: E402

isolate("personal")
import nb_gateway  # noqa: E402
import personal_keys as K  # noqa: E402


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


KEY_B = "sk-mine-BBBB2222"


def gemini(req):
    k = req.headers.get("x-goog-api-key")
    return (200, {"models": [{"name": "models/gemini-3-pro-image"}]}) if k == "k1" else (400, {"error": {"message": "API key not valid"}})


def openai(req):
    return (200, {"data": []}) if req.headers.get("authorization") == "Bearer sk-good1234" else (401, {"error": {"message": "Incorrect API key"}})


def ark(req):
    if req.headers.get("authorization") != "Bearer k3-good123":
        return 401, {"error": {"code": "AuthenticationError"}}
    if (req.body or {}).get("model") == "dola-seedream-5-5-261028":
        return 404, {"error": {"code": "InvalidEndpointOrModel.NotFound"}}
    return 400, {"error": {"code": "InvalidParameter", "message": "size"}}


fake = FakeHTTP({("GET", "/v1beta/models"): gemini, ("GET", "/v1/models"): openai,
                 ("POST", "/api/v3/images/generations"): ark})
os.environ["NANOBANANA_TEST_GEMINI_BASE"] = fake.base
os.environ["NANOBANANA_TEST_OPENAI_BASE"] = fake.base

# Review Focus 1 - what people paste
check("trim", K.clean_key("  AIzaXYZ12345\n") == ("AIzaXYZ12345", None))
check("quotes", K.clean_key('"sk-abc12345"') == ("sk-abc12345", None))
check("bearer", K.clean_key("Bearer ark-abc12345") == ("ark-abc12345", None))
check("inner space", K.clean_key("sk-abc 12345")[1] == "bad_key")
check("not text", K.clean_key({"value": "x"})[1] == "bad_key")
check("too short", K.clean_key("abc")[1] == "bad_key")
check("non-ascii", K.clean_key("sk-키키키키키키")[1] == "bad_key")

r = K.verify("studio", "k1")
last = fake.calls[-1]
check("studio ok", r["result"] == "ok", r)
check("studio key in header, not in the address", last.headers.get("x-goog-api-key") == "k1" and "k1" not in last.raw_path)
check("studio invalid 400", K.verify("studio", "bad12345")["result"] == "invalid")
check("openai ok", K.verify("openai", "sk-good1234")["result"] == "ok")
check("openai invalid 401", K.verify("openai", "sk-bad12345")["result"] == "invalid")
r = K.verify("ark", "k3-good123", ark_models={"seedream-5-5-261028": "dola-seedream-5-5-261028",
                                                "seedream-4-5-251128": "seedream-4-5-251128"},
             ark_base=fake.base + "/api/v3")
check("ark per model", r["result"] == "ok" and r["models_off"] == ["seedream-5-5-261028"], r)
check("ark probe never asks for a real size", all((c.body or {}).get("size") == "1x1" for c in fake.calls if c.path.endswith("/generations")))
check("ark bad key", K.verify("ark", "nope12345", ark_models={"seedream-4-5-251128": "seedream-4-5-251128"},
                              ark_base=fake.base + "/api/v3")["result"] == "invalid")
os.environ["NANOBANANA_TEST_OPENAI_BASE"] = "http://127.0.0.1:9"
check("net", K.verify("openai", "sk-good1234")["result"] == "net")
os.environ["NANOBANANA_TEST_OPENAI_BASE"] = fake.base
check("reason never carries the key", "sk-bad12345" not in json.dumps(K.verify("openai", "sk-bad12345")))

check("no company-key check left", not hasattr(K, "company_match") and not hasattr(K, "fingerprint_prefix"))

REC = {"v": 1, "keys": {"openai": {"key": KEY_B, "state": "ok", "checked_at": "x", "models_off": []},
                        "ark": {"key": "k3-good123", "state": "unchecked", "checked_at": "", "models_off": ["seedream-5-5-261028"]}}}
dd = tempfile.mkdtemp(prefix="nb_t_vault_")
check("vault roundtrip", K.save(dd, REC) and K.load(dd) == ("ok", REC))
vault = os.path.join(dd, K.VAULT_NAME + nb_gateway.VAULT_SUFFIX)
check("vault file has no key text", os.path.isfile(vault) and KEY_B.encode() not in open(vault, "rb").read())
check("missing", K.load(tempfile.mkdtemp()) == ("missing", None))
bad = tempfile.mkdtemp()
open(os.path.join(bad, K.VAULT_NAME + nb_gateway.VAULT_SUFFIX), "wb").write(b"garbage not dpapi")
check("unreadable", K.load(bad) == ("unreadable", None))
st = K.public_status(REC)
check("status no secrets", KEY_B not in json.dumps(st) and st["openai"]["tail"] == "2222" and st["openai"]["set"] and not st["studio"]["set"])
check("status models_off", st["ark"]["models_off"] == ["seedream-5-5-261028"] and st["ark"]["state"] == "unchecked")
check("client keys", K.client_keys(REC) == {"studio": "", "openai": KEY_B, "ark": "k3-good123", "vertex": None})
done()
