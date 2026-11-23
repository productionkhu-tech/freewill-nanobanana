# -*- coding: utf-8 -*-
"""Task 1: which edition a run is, and each edition's names (spec section 4, 5.1).
usage: python tests/personal/t_edition.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _h import REPO, check, done, run_py  # noqa: E402

sys.path.insert(0, REPO)
from edition import detect  # noqa: E402


def bundle(content):
    d = tempfile.mkdtemp(prefix="nb_t_mei_")
    if content is not None:
        with open(os.path.join(d, "EDITION"), "w", encoding="utf-8") as f:
            f.write(content)
    return d


check("frozen + EDITION=personal", detect(True, bundle("personal\n"), {}) == "personal")
check("frozen, no file -> company", detect(True, bundle(None), {}) == "company")
check("frozen ignores env", detect(True, bundle(None), {"NANOBANANA_EDITION": "personal"}) == "company")
check("frozen, file says other", all(detect(True, bundle(c), {}) == "company" for c in ("company\n", "Personal", "")))
check("source + env", detect(False, None, {"NANOBANANA_EDITION": "personal"}) == "personal")
check("source default", detect(False, None, {}) == "company")

NAMES = ("EDITION PERSONAL APP_TITLE MINI_TITLE PORT MUTEX_NAME APP_USER_MODEL_ID DATA_DIRNAME PROJECTS_DIRNAME "
         "CLIPBOARD_DIRNAME OUTPUT_DIRNAME DOCK_NAME DOCK_RANK REPO EXE_STEM UPDATE_LOG").split()
COMPANY_EXPECTED = {
    "EDITION": "company", "PERSONAL": False, "APP_TITLE": "NanoBanana", "MINI_TITLE": "NanoBanana mini", "PORT": 5656,
    "MUTEX_NAME": "NanoBanana-AIImageStudio-SingleInstance-v1", "APP_USER_MODEL_ID": "NanoBanana.AIImageStudio",
    "DATA_DIRNAME": ".nanobanana", "PROJECTS_DIRNAME": "NanoBanana JSON", "CLIPBOARD_DIRNAME": "NanoBanana Clipboard",
    "OUTPUT_DIRNAME": "NanoBanana_Output", "DOCK_NAME": "nanobanana", "DOCK_RANK": 1,
    "REPO": "productionkhu-tech/freewill-nanobanana", "EXE_STEM": "NanoBanana", "UPDATE_LOG": "nanobanana_update.log"}
PERSONAL_EXPECTED = {
    "EDITION": "personal", "PERSONAL": True, "APP_TITLE": "NanoBanana Personal", "MINI_TITLE": "NanoBanana Personal mini",
    "PORT": 5657, "MUTEX_NAME": "NanoBanana-Personal-SingleInstance-v1", "APP_USER_MODEL_ID": "NanoBanana.Personal",
    "DATA_DIRNAME": ".nanobanana-personal", "PROJECTS_DIRNAME": "NanoBanana Personal JSON",
    "CLIPBOARD_DIRNAME": "NanoBanana Personal Clipboard", "OUTPUT_DIRNAME": "NanoBanana Personal_Output",
    "DOCK_NAME": "nanobanana-personal", "DOCK_RANK": 1.5, "REPO": "productionkhu-tech/freewill-nanobanana-personal",
    "EXE_STEM": "NanoBanana-Personal", "UPDATE_LOG": "nanobanana_personal_update.log"}
TITLES = [("NanoBanana", True, False), ("NanoBanana — a *", True, False), ("NanoBanana mini", False, False),
          ("NanoBanana Personal", False, True), ("NanoBanana Personal — a", False, True),
          ("NanoBanana Personal mini", False, False), ("Image Viewer", False, False)]


def info(ed):
    code = ("import edition as E\n"
            "print(json.dumps({'names': {n: getattr(E, n, None) for n in %r},"
            " 'titles': [E.is_own_main_title(t) if hasattr(E, 'is_own_main_title') else None for t in %r]}))"
            % (NAMES, [t[0] for t in TITLES]))
    return run_py(ed, code)


ci, pi = info("company"), info("personal")
check("company names unchanged", ci.get("names") == COMPANY_EXPECTED, ci.get("_error", ""))
check("personal names", pi.get("names") == PERSONAL_EXPECTED, pi.get("_error", ""))
for i, (t, comp, pers) in enumerate(TITLES):
    check("title %r" % t, (ci.get("titles") or [None] * 9)[i] is comp and (pi.get("titles") or [None] * 9)[i] is pers)
done()
