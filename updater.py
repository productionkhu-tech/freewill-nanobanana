# -*- coding: utf-8 -*-
"""
NanoBanana Auto-Updater
On launch, compares local VERSION with remote VERSION on GitHub main branch.
If newer, downloads the zipball and overlays updatable files before restart.

Note: because the app runs from a PyInstaller-bundled EXE whose source is
inside _MEIPASS (read-only, temp), we persist updates to a writable location
next to the EXE (dist/NanoBanana/user_updates/). The launcher checks that
location on boot and prefers user_updates/ files when present.
"""

import os
import sys
import shutil
import zipfile
import tempfile
import urllib.request

REPO = "productionkhu-tech/freewill-nanobanana"
BRANCH = "main"
REMOTE_VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/VERSION"
ZIPBALL_URL = f"https://github.com/{REPO}/archive/refs/heads/{BRANCH}.zip"

UPDATABLE_FILES = {
    "app.py", "launcher.py", "updater.py", "requirements.txt", "VERSION",
    "install.bat", "setup_env.bat", "app.ico", "images.png",
}
UPDATABLE_DIRS = {"templates", "static"}


def _app_dir():
    """Directory to write updates to.
    - When frozen (EXE): next to the EXE so future launches can overlay.
    - When running from source: the source dir itself.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _version_file():
    # Prefer user_updates/VERSION if it exists (post-update overlay)
    app_dir = _app_dir()
    ov = os.path.join(app_dir, "user_updates", "VERSION")
    if os.path.isfile(ov):
        return ov
    # Fall back to bundled VERSION
    if getattr(sys, "frozen", False):
        bundled = os.path.join(sys._MEIPASS, "VERSION")
        if os.path.isfile(bundled):
            return bundled
    return os.path.join(app_dir, "VERSION")


def get_current_version():
    try:
        with open(_version_file(), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "v0000-00-0000"


def get_remote_version():
    req = urllib.request.Request(
        REMOTE_VERSION_URL,
        headers={"User-Agent": "NanoBanana-Updater"},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return resp.read().decode("utf-8").strip()


def check_for_update():
    """Returns (has_update, current, remote)."""
    current = get_current_version()
    try:
        remote = get_remote_version()
    except Exception as e:
        print(f"  remote version fetch failed: {e}")
        return False, current, current
    # Simple string comparison works for v2026-04-1701 style (zero-padded)
    has_update = bool(remote) and remote != current and remote > current
    return has_update, current, remote


def download_and_overlay():
    """Download main-branch zipball, extract updatable pieces into
    <app_dir>/user_updates/. Returns True on success."""
    app_dir = _app_dir()
    overlay_dir = os.path.join(app_dir, "user_updates")
    os.makedirs(overlay_dir, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "update.zip")
        extract_dir = os.path.join(tmpdir, "extracted")

        req = urllib.request.Request(
            ZIPBALL_URL, headers={"User-Agent": "NanoBanana-Updater"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            with open(zip_path, "wb") as f:
                shutil.copyfileobj(resp, f)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        entries = os.listdir(extract_dir)
        if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
            source_dir = os.path.join(extract_dir, entries[0])
        else:
            source_dir = extract_dir

        for name in os.listdir(source_dir):
            src = os.path.join(source_dir, name)
            dst = os.path.join(overlay_dir, name)
            if os.path.isfile(src) and name in UPDATABLE_FILES:
                shutil.copy2(src, dst)
            elif os.path.isdir(src) and name in UPDATABLE_DIRS:
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)

    return True


def run_update_check():
    """Check + overlay; returns True if an update was applied (caller should restart)."""
    has_update, current, remote = check_for_update()
    print(f"  Version local={current} remote={remote}")
    if not has_update:
        return False
    print(f"  Update available: {remote} — downloading...")
    try:
        if download_and_overlay():
            print(f"  Overlaid. Will restart.")
            return True
    except Exception as e:
        print(f"  Update failed: {e}")
    return False
