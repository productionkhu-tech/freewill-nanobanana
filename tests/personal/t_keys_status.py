# -*- coding: utf-8 -*-
"""The personal edition's key state on screen, and keys surviving an update (2026-11-24).

1) The status dots at the top right are the one live place that shows each company's key (user 11/24: the
   my-keys button needs no chips - the top-right colours prove it, live): no key = grey, checked = green,
   not checked yet = yellow, rejected (at the check or while generating) = red. The company edition's dots
   are untouched.
2) An update only swaps the EXE: the keys stay in ~/.nanobanana-personal/personal_keys.vault, sealed with
   DPAPI for this Windows account and a fixed entropy. If any of those names changes, every person's saved
   keys silently vanish on the next update - so they are pinned here, and a vault written the way the
   released build writes it must open in this build without being rewritten.
usage: python tests/personal/t_keys_status.py"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _h import check, done, isolate, run_py  # noqa: E402

P = isolate("personal")
import app as A  # noqa: E402
import nb_gateway as G  # noqa: E402
import personal_keys as K  # noqa: E402

A._init_keys = lambda: None
A.init_app()
A.shared.session_restoring = False
c = A.app.test_client()
H = {"X-NB-Token": A.CSRF_TOKEN}


def set_keys(states, phase="ready"):
    keys = {p: {"key": "demo-%s-key-%s" % (p, s), "state": s, "checked_at": "", "models_off": []}
            for p, s in states.items()}
    with A._personal_lock:
        A._personal["rec"] = {"v": 1, "keys": keys}
        A._personal["phase"] = phase
    A._apply_keys(K.client_keys(A._personal["rec"]), "personal")


def dots():
    d = c.get("/api/status", headers=H).get_json()
    return d.get("studio"), d.get("openai"), d.get("seedream")


# 1) the top-right dots follow the key state
set_keys({})
check("no keys: all grey", dots() == ("disconnected", "disconnected", "disconnected"), dots())
set_keys({"studio": "ok", "openai": "ok", "ark": "ok"})
check("three checked keys: all green", dots() == ("connected", "connected", "connected"), dots())
set_keys({"studio": "ok", "openai": "rejected", "ark": "unchecked"})
check("rejected = red, not checked yet = yellow", dots() == ("connected", "error", "checking"), dots())
set_keys({"studio": "ok", "openai": "ok"})
A._personal_mark_rejected("OpenAI")                  # what a 401 while generating does
check("a key turned down while generating goes red at once", dots()[1] == "error", dots())

# the company edition keeps its own dots
r = run_py("company", "import app as A\nA._init_keys = lambda: None\nA.init_app()\n"
           "A.shared.studio_status = 'connected'; A.shared.openai_status = 'error'; A.shared.seedream_status = 'disconnected'\n"
           "d = A.app.test_client().get('/api/status').get_json()\n"
           "print(json.dumps([d['studio'], d['openai'], d['seedream']]))")
check("company edition: dots unchanged", r == ["connected", "error", "disconnected"], r)

# the button carries no per-company chips any more (the dots do that job)
html = open(os.path.join(os.path.dirname(A.__file__), "templates", "index.html"), encoding="utf-8").read()
css = open(os.path.join(os.path.dirname(A.__file__), "static", "style.css"), encoding="utf-8").read()
check("my-keys button: no chips", "myKeysChips" not in html and "mk-chip" not in css)
check("a yellow dot exists for 'not checked yet'", ".dot.checking" in css)

# 2) keys survive an update
check("vault names pinned (changing one loses everyone's keys on update)",
      (K.VAULT_NAME, G.VAULT_SUFFIX, G._VAULT_ENTROPY, A._ed.DATA_DIRNAME)
      == ("personal_keys", ".vault", b"NanoBanana/gateway/v1", ".nanobanana-personal"))
data = A._user_data_dir()
check("keys live in the personal data folder, not next to the EXE",
      os.path.normcase(data) == os.path.normcase(os.environ["NANOBANANA_DATA_DIR"]))
old = {"v": 1, "keys": {"studio": {"key": "AIza-demo-1111", "state": "ok", "checked_at": "2026-11-24T11:29:11",
                                   "models_off": []},
                        "ark": {"key": "ark-demo-2222", "state": "ok", "checked_at": "2026-11-24T11:29:11",
                                "models_off": ["seedream-5-5-261028"]}}}
check("released build's way of writing the vault", K.save(data, old))
vf = os.path.join(data, K.VAULT_NAME + G.VAULT_SUFFIX)
before = hashlib.sha256(open(vf, "rb").read()).hexdigest()
st, rec = K.load(data)
check("opens in this build", st == "ok" and rec == old, st)
pub = K.public_status(rec)
check("shows the same keys (tail + state + locked models)",
      pub["studio"]["tail"] == "1111" and pub["ark"]["models_off"] == ["seedream-5-5-261028"]
      and pub["openai"]["set"] is False, pub)
# start-up after the update: reads it, re-checks only 'unchecked' keys - checked keys are not re-sent and
# the file is not rewritten
A._init_personal_keys(background=False)
pub = K.public_status(A._personal["rec"])
check("start-up after the update: same keys, ready", A._personal["phase"] == "ready"
      and pub["studio"]["tail"] == "1111" and pub["ark"]["set"], (A._personal["phase"], pub))
check("start-up leaves the vault file as it was",
      hashlib.sha256(open(vf, "rb").read()).hexdigest() == before)
check("and its dots are green straight away", dots() == ("connected", "disconnected", "connected"), dots())
done()
