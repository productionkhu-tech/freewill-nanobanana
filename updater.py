# -*- coding: utf-8 -*-
"""
NanoBanana Auto-Updater
Checks GitHub releases for newer version and applies updates.
"""

import os
import sys
import json
import shutil
import zipfile
import tempfile
import urllib.request

REPO = "productionkhu-tech/freewill-nanobanana"
VERSION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")
GITHUB_API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"

# Files that should be updated (never touch user data/credentials)
UPDATABLE_FILES = {
    "app.py", "launcher.py", "updater.py", "requirements.txt", "VERSION",
    "install.bat", "setup_env.bat",
}
UPDATABLE_DIRS = {"templates", "static"}


def get_current_version():
    try:
        with open(VERSION_FILE, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "v0000-00-0000"


def check_for_update():
    """Returns (has_update, latest_version, download_url) or raises on network error."""
    req = urllib.request.Request(
        GITHUB_API_URL,
        headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "NanoBanana-Updater"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    latest = data.get("tag_name", "")
    current = get_current_version()

    if not latest:
        return False, current, ""

    has_update = latest > current
    download_url = data.get("zipball_url", "")
    return has_update, latest, download_url


def download_and_apply(download_url, latest_version=""):
    """Download release zip, extract, and overwrite app files. Returns True on success."""
    app_dir = os.path.dirname(os.path.abspath(__file__))

    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, "update.zip")
        extract_dir = os.path.join(tmpdir, "extracted")

        # Download
        req = urllib.request.Request(
            download_url,
            headers={"User-Agent": "NanoBanana-Updater"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            with open(zip_path, "wb") as f:
                f.write(resp.read())

        # Extract
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        # GitHub zips have a top-level directory like "owner-repo-hash/"
        entries = os.listdir(extract_dir)
        if len(entries) == 1 and os.path.isdir(os.path.join(extract_dir, entries[0])):
            source_dir = os.path.join(extract_dir, entries[0])
        else:
            source_dir = extract_dir

        # Copy updatable files
        for name in os.listdir(source_dir):
            src = os.path.join(source_dir, name)
            dst = os.path.join(app_dir, name)

            if os.path.isfile(src) and name in UPDATABLE_FILES:
                shutil.copy2(src, dst)
            elif os.path.isdir(src) and name in UPDATABLE_DIRS:
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)

        # Clear __pycache__
        cache_dir = os.path.join(app_dir, "__pycache__")
        if os.path.isdir(cache_dir):
            shutil.rmtree(cache_dir, ignore_errors=True)

    return True


def run_update_check():
    """Convenience function: check + apply + return restart flag."""
    current = get_current_version()
    print(f"  Version: {current}")

    try:
        has_update, latest, url = check_for_update()
    except Exception as e:
        print(f"  Update check failed: {e}")
        return False

    if not has_update:
        print(f"  Up to date.")
        return False

    print(f"  Update available: {latest}")
    print(f"  Downloading...")

    try:
        if download_and_apply(url, latest):
            print(f"  Updated to {latest}! Restarting...")
            return True
        else:
            print(f"  Update failed, continuing with current version.")
            return False
    except Exception as e:
        print(f"  Update failed: {e}")
        return False
