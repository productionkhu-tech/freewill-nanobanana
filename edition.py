# -*- coding: utf-8 -*-
"""Which NanoBanana this is - the company app or the personal edition (v2026-11-23 design, spec section 4).

One source, two EXEs. The personal EXE carries a data file `EDITION` reading `personal` (only
NanoBananaPersonal.spec bundles it); an installed EXE never takes the edition from the environment, so
nobody can turn a company EXE into a personal one (or the other way) on a company PC. Source runs read
NANOBANANA_EDITION=personal - that is for the isolated tests.

Every per-edition name lives here and nowhere else: launcher, updater, app and mini_window read these.
Keep this module import-free of the app (it is read first, by everything)."""
import os
import sys


def detect(frozen, meipass, env):
    """'personal' or 'company'."""
    if frozen:
        try:
            with open(os.path.join(meipass or "", "EDITION"), "r", encoding="utf-8") as f:
                return "personal" if f.read().strip() == "personal" else "company"
        except Exception:
            return "company"
    return "personal" if (env.get("NANOBANANA_EDITION") or "").strip() == "personal" else "company"


EDITION = detect(bool(getattr(sys, "frozen", False)), getattr(sys, "_MEIPASS", None), os.environ)
PERSONAL = EDITION == "personal"

if PERSONAL:
    APP_TITLE = "NanoBanana Personal"
    # Outside Windows' dynamic port range on default PCs (49152+) AND on PCs where it was widened down
    # (this dev PC: 1024-15000) - Hyper-V / WSL reserve 100-port blocks inside that range at boot, and a
    # reserved port cannot be bound (WinError 10013). 5657 was inside a reserved block here on 2026-11-23.
    PORT = 35657
    MUTEX_NAME = "NanoBanana-Personal-SingleInstance-v1"
    APP_USER_MODEL_ID = "NanoBanana.Personal"
    DATA_DIRNAME = ".nanobanana-personal"
    PROJECTS_DIRNAME = "NanoBanana Personal JSON"
    CLIPBOARD_DIRNAME = "NanoBanana Personal Clipboard"
    OUTPUT_DIRNAME = "NanoBanana Personal_Output"
    DOCK_NAME, DOCK_RANK = "nanobanana-personal", 1.5
    REPO = "productionkhu-tech/freewill-nanobanana-personal"
    EXE_STEM = "NanoBanana-Personal"
    UPDATE_LOG = "nanobanana_personal_update.log"
else:
    APP_TITLE = "NanoBanana"
    PORT = 5656
    MUTEX_NAME = "NanoBanana-AIImageStudio-SingleInstance-v1"
    APP_USER_MODEL_ID = "NanoBanana.AIImageStudio"
    DATA_DIRNAME = ".nanobanana"
    PROJECTS_DIRNAME = "NanoBanana JSON"
    CLIPBOARD_DIRNAME = "NanoBanana Clipboard"
    OUTPUT_DIRNAME = "NanoBanana_Output"
    DOCK_NAME, DOCK_RANK = "nanobanana", 1
    REPO = "productionkhu-tech/freewill-nanobanana"
    EXE_STEM = "NanoBanana"
    UPDATE_LOG = "nanobanana_update.log"

MINI_TITLE = APP_TITLE + " mini"
_PERSONAL_TITLE = "NanoBanana Personal"


def is_own_main_title(title):
    """Is this window title our edition's main window? The page renames it ("NanoBanana - project *"), so
    match the start - but the company app must not take the personal window (same first word), and
    neither may take a mini window (always on top: it comes first in the enumeration)."""
    t = title or ""
    if t == MINI_TITLE:
        return False
    if PERSONAL:
        return t.startswith(_PERSONAL_TITLE)
    return t.startswith("NanoBanana") and not t.startswith(_PERSONAL_TITLE)
