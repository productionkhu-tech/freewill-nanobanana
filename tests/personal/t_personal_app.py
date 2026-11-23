# -*- coding: utf-8 -*-
"""Task 5: the personal edition's keys inside the app (spec 5.2, 5.3, 5.4).

Runs as an employee PC would look: company-looking keys in the environment, a company key vault and
gateway token in ~/.nanobanana. The personal edition must use none of it, call nb_gateway only for the
gateway address and its own vault, and send the gateway nothing but /personal/check.
usage: python tests/personal/t_personal_app.py"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _h import FakeHTTP, check, done, isolate  # noqa: E402

P = isolate("personal")


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


COMPANY_KEY = "AIzaCompanyStudio1234"
company_set = {sha(COMPANY_KEY)}


def gw(req):
    p = (req.body or {}).get("p", "")
    return 200, {"ok": True, "hashes": [h for h in company_set if h.startswith(p)]}


def openai(req):
    return (200, {"data": []}) if req.headers.get("authorization") == "Bearer sk-good1234" else (401, {"error": {"message": "bad"}})


def gemini(req):
    return (200, {"models": []}) if req.headers.get("x-goog-api-key") == "AIzaMine12345678" else (400, {"error": {"message": "bad"}})


def ark(req):
    if req.headers.get("authorization") != "Bearer ark-k1234567":
        return 401, {}
    if (req.body or {}).get("model") == "dola-seedream-5-5-261028":
        return 404, {}
    return 400, {}


fake = FakeHTTP({("GET", "/v1/models"): openai, ("GET", "/v1beta/models"): gemini,
                 ("POST", "/api/v3/images/generations"): ark})
fake_gw = FakeHTTP({("POST", "/personal/check"): gw})
DOWN = "http://127.0.0.1:9"


def providers(up):
    os.environ["NANOBANANA_TEST_OPENAI_BASE"] = fake.base if up else DOWN
    os.environ["NANOBANANA_TEST_GEMINI_BASE"] = fake.base if up else DOWN
    os.environ["NANOBANANA_TEST_ARK_BASE"] = (fake.base + "/api/v3") if up else DOWN
    os.environ["NANOBANANA_GATEWAY_URL"] = fake_gw.base if up else DOWN
    os.environ["OPENAI_BASE_URL"] = (fake.base + "/v1") if up else DOWN   # the SDK's own self-test stays local


providers(True)
# --- an employee PC
os.environ.update({"OPENAI_API_KEY": "sk-companyenv-1111", "ARK_API_KEY": "ark-companyenv-2222",
                   "NANOBANANA_STUDIO_KEY": "AIzaCompanyEnv3333", "NANOBANANA_PROJECT_ID": "company-proj"})
sa = os.path.join(P["work"], "sa.json")
open(sa, "w").write(json.dumps({"type": "service_account", "client_email": "x@company.iam"}))
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = sa
co = os.path.join(P["home"], ".nanobanana")
os.makedirs(co)
for n in ("gw_keys.vault", "gw_token.vault", "gateway.json"):
    open(os.path.join(co, n), "wb").write(b"company")

import nb_gateway  # noqa: E402
import inspect  # noqa: E402
gw_calls = []
for _n, _f in list(vars(nb_gateway).items()):
    if inspect.isfunction(_f) and not _n.startswith("_") and _f.__module__ == "nb_gateway":
        def _wrap(f=_f, n=_n):
            def w(*a, **k):
                gw_calls.append(n)
                return f(*a, **k)
            return w
        setattr(nb_gateway, _n, _wrap())

import app as A  # noqa: E402
applied = []
_orig_apply = A._apply_keys
A._apply_keys = lambda keys, source: (applied.append(dict(keys)), _orig_apply(keys, source))[1]
A.PERSONAL_RETRY_S = 0.01
A.init_app()
A._personal_recheck()
c = A.app.test_client()
H = {"X-NB-Token": A.CSRF_TOKEN}


def post(body):
    return c.post("/api/personal/keys", json=body, headers=H).get_json()


def keys():
    return c.get("/api/personal/keys", headers=H).get_json()["keys"]


def restart():
    A._init_personal_keys(background=False)


check("company zero: nb_gateway calls", set(gw_calls) <= {"gateway_url", "vault_get", "vault_put"}, sorted(set(gw_calls)))
check("company zero: no env keys used", applied and applied[-1] == {"studio": "", "openai": "", "ark": "", "vertex": None}, applied[-1:])
check("company zero: no usage spool", not os.path.exists(os.path.join(P["data"], "usage_spool.jsonl")))
check("company zero: company folder untouched", sorted(os.listdir(co)) == ["gateway.json", "gw_keys.vault", "gw_token.vault"])
check("status says personal", c.get("/api/status", headers=H).get_json()["gw"]["state"] == "personal")

r = post({"provider": "openai", "key": " sk-good1234 "})
check("save ok", r.get("ok") and r.get("state") == "ok" and applied[-1]["openai"] == "sk-good1234", r)
check("client built", A.shared.client_openai is not None)
r = post({"provider": "openai", "key": "sk-bad12345"})
check("invalid not saved", r.get("error") == "invalid_key" and keys()["openai"]["tail"] == "1234", r)
r = post({"provider": "studio", "key": COMPANY_KEY})
check("company refused", r.get("error") == "company_key" and not keys()["studio"]["set"], r)
check("bad provider", post({"provider": "vertex", "key": "abcdefgh123"}).get("error") == "bad_provider")
check("bad key", post({"provider": "openai", "key": "sk 1"}).get("error") == "bad_key")
check("gateway saw prefixes only", all(set((x.body or {}).keys()) == {"p"} and len(x.body["p"]) == 8 for x in fake_gw.calls))
check("gateway paths", {x.path for x in fake_gw.calls} == {"/personal/check"})

# Review Focus 2 - a key put in while offline
providers(False)
r = post({"provider": "ark", "key": "ark-k1234567"})
check("offline: saved, not checked yet", r.get("ok") and r.get("state") == "unchecked", r)
check("offline: company unknown", keys()["ark"]["company"] == "unknown")
providers(True)
restart()
check("next start: checked", keys()["ark"]["state"] == "ok" and keys()["ark"]["company"] == "clear", keys()["ark"])
check("next start: seedream 5.5 locked", keys()["ark"]["models_off"] == ["seedream-5-5-261028"])
A.current_project().model = "seedream-5-5-261028"
g = c.post("/api/generate", headers=H).get_json()
check("locked seedream refused before sending", (g.get("error") or "").startswith("BytePlus 콘솔"), g)
company_set.add(sha("ark-k1234567"))
restart()
check("company key found at start -> removed", not keys()["ark"]["set"] and applied[-1]["ark"] == "")
company_set.discard(sha("ark-k1234567"))

# a provider turns the key down while generating
check("auth error marks the key rejected, no gateway reload", A._keys_changed_since(A._keys_rt["ids"], "OpenAI") is False
      and keys()["openai"]["state"] == "rejected")

DELETE = c.delete("/api/personal/keys", json={"provider": "openai"}, headers=H).get_json()
check("delete", DELETE.get("ok") and not keys()["openai"]["set"] and A.shared.client_openai is None)
A.current_project().model = "gpt-image-2"
g = c.post("/api/generate", headers=H).get_json()
check("no key -> refused in words", "내 키" in (g.get("error") or ""), g)

# Review Focus 3 - a vault this account cannot open
vault = os.path.join(P["data"], "personal_keys" + nb_gateway.VAULT_SUFFIX)
open(vault, "wb").write(b"from another PC")
restart()
check("unreadable vault -> phase", c.get("/api/personal/keys", headers=H).get_json()["phase"] == "unreadable")
check("status carries the phase", c.get("/api/status", headers=H).get_json()["gw"]["phase"] == "unreadable")
check("saving still works", post({"provider": "openai", "key": "sk-good1234"}).get("ok"))
check("...and is readable again", c.get("/api/personal/keys", headers=H).get_json()["phase"] == "ready")

check("hint org", "조직 인증" in A._personal_hint("Error code: 403 - organization must be verified to use gpt-image-2"))
check("hint gemini billing", "결제" in A._personal_hint("429 RESOURCE_EXHAUSTED quota ... limit: 0, model: gemini-3-pro-image"))
check("hint auth", "다시 넣어" in A._personal_hint("Error code: 401 - Incorrect API key provided"))
check("hint passes others", A._personal_hint("500 internal") == "500 internal")
blob = json.dumps(list(getattr(A.state, "logs", [])), default=str, ensure_ascii=False) + json.dumps(c.get("/api/logs?all=1", headers=H).get_json())
check("no key text in logs", "sk-good1234" not in blob and "ark-k1234567" not in blob)
check("company zero at the end", set(gw_calls) <= {"gateway_url", "vault_get", "vault_put"}, sorted(set(gw_calls)))

from _h import run_py  # noqa: E402
r = run_py("company", "import app as A\nc = A.app.test_client()\n"
           "print(json.dumps({'s': c.get('/api/personal/keys').status_code,"
           " 'h': A._personal_hint('organization must be verified'),"
           " 'gw': c.get('/api/status').get_json()['gw'].get('state')}))")
check("company: no key route", r.get("s") == 404, r)
check("company: errors unchanged", r.get("h") == "organization must be verified")
check("company: key-server state as before", r.get("gw") != "personal")
done()
