# -*- coding: utf-8 -*-
"""Task 6: the personal edition has no team/project gate and no agent path; project files keep the
company team/project they carry (spec 5.2). Also Review Focus 4: a key deleted while a batch runs.
Generation goes to a local fake OpenAI (no money). usage: python tests/personal/t_personal_paths.py"""
import base64
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _h import FakeHTTP, check, done, isolate, run_py  # noqa: E402

P = isolate("personal")
from PIL import Image  # noqa: E402

buf = io.BytesIO()
Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, "PNG")
PNG_B64 = base64.b64encode(buf.getvalue()).decode("ascii")


def images(req):
    time.sleep(0.8)
    return 200, {"created": 1, "data": [{"b64_json": PNG_B64}],
                 "usage": {"input_tokens": 5, "output_tokens": 50, "total_tokens": 55,
                           "input_tokens_details": {"text_tokens": 5, "image_tokens": 0}}}


fake = FakeHTTP({("GET", "/v1/models"): lambda r: (200, {"data": []}),
                 ("POST", "/v1/images/generations"): images})
os.environ.update({"NANOBANANA_TEST_OPENAI_BASE": fake.base, "OPENAI_BASE_URL": fake.base + "/v1",
                   "NANOBANANA_GATEWAY_URL": fake.base})

import app as A  # noqa: E402
A.init_app()
c = A.app.test_client()
H = {"X-NB-Token": A.CSRF_TOKEN}
check("key saved", c.post("/api/personal/keys", json={"provider": "openai", "key": "sk-good1234"}, headers=H).get_json().get("ok"))

p = A.current_project()
p.model = "gpt-image-2"
p.count = 1
p.prompt_sections = ["a red square"]
g = c.post("/api/generate", headers=H).get_json()
check("window generate not blocked by billing", g.get("ok") is True and not g.get("needs_billing"), g)
t0 = time.time()
while p.is_generating and time.time() - t0 < 60:
    time.sleep(0.2)
check("...and it made the image", p.done_count >= 1, (p.done_count, p.fail_count))

pid = p.pid
n_before = len(fake.calls)
r = c.post("/api/generate", json={"pid": pid, "prompt": "x", "settings": {"model": "gpt-image-2"}}, headers=H)
check("agent pid refused", r.status_code == 403 and r.get_json().get("error") == "not_in_personal", r.get_json())
for extra in ({"batch_id": "abcd1234"}, {"check_only": True}):
    r = c.post("/api/generate", json=extra, headers=H)
    check("agent field %s refused" % list(extra)[0], r.status_code == 403 and r.get_json().get("error") == "not_in_personal")
time.sleep(1.0)
check("nothing was sent for them", not any(x.path.endswith("/images/generations") for x in fake.calls[n_before:]))
pj = c.get("/api/projects", headers=H).get_json()
check("features empty", pj.get("features") == [] and pj.get("agent_pid") is False, pj.get("features"))
check("tab never marked as missing billing", all(x.get("billing_confirmed") for x in pj["projects"]))
for path in ("/api/billing/catalog", "/api/billing/state"):
    check("%s 404" % path, c.get(path, headers=H).status_code == 404)
check("/api/billing/set 404", c.post("/api/billing/set", json={"team_id": "t", "project_id": "p"}, headers=H).status_code == 404)

# Review Focus 4 - the key goes away while a batch runs
p.count = 5
g = c.post("/api/generate", headers=H).get_json()
check("batch of 5 started", g.get("ok"), g)
t0 = time.time()
while not any(x.path.endswith("/images/generations") for x in fake.calls[-3:]) and time.time() - t0 < 20:
    time.sleep(0.05)
check("delete during the batch", c.delete("/api/personal/keys", json={"provider": "openai"}, headers=H).get_json().get("ok"))
t0 = time.time()
while p.is_generating and time.time() - t0 < 60:
    time.sleep(0.2)
b = c.get("/api/batch?pid=%s&batch_id=%s" % (pid, g.get("batch_id")), headers=H).get_json()
errs = b.get("errors") or []
check("the rest fail with the key-needed message", b.get("failed", 0) >= 1 and errs and all("키" in str(e) for e in errs), b)
check("nothing hung", not p.is_generating)
logs = json.dumps(c.get("/api/logs?all=1", headers=H).get_json(), ensure_ascii=False)
check("no traceback", "Traceback" not in logs)

# project files: a company file keeps its team/project; a personal file has none
cofile = os.path.join(P["work"], "company.json")
p.save_project(os.path.join(P["work"], "seed.json"))
seed = json.load(open(os.path.join(P["work"], "seed.json"), encoding="utf-8"))
ui_key = "ui_state"
seed[ui_key]["billing_team_id"], seed[ui_key]["billing_project_id"] = "T-COMPANY", "PJ-COMPANY"
json.dump(seed, open(cofile, "w", encoding="utf-8"))
q = A.create_project(activate=True)
q.load_project(cofile)
q.save_project(cofile)
back = json.load(open(cofile, encoding="utf-8"))[ui_key]
check("company billing fields preserved", (back.get("billing_team_id"), back.get("billing_project_id")) == ("T-COMPANY", "PJ-COMPANY"), back.get("billing_team_id"))
newfile = os.path.join(P["work"], "personal_new.json")
A.create_project(activate=True).save_project(newfile)
nb = json.load(open(newfile, encoding="utf-8"))[ui_key]
check("personal new file has no billing", not nb.get("billing_team_id") and not nb.get("billing_project_id"))
r = run_py("company", "import app as A\np = A.create_project(activate=True)\np.load_project(%r)\n"
                      "print(json.dumps({'confirmed': bool(p.billing_confirmed)}))" % newfile)
check("company asks again for a personal file", r.get("confirmed") is False, r)
done()
