# -*- coding: utf-8 -*-
"""
NanoBanana Auto-Updater — whole-EXE swap approach.

Strategy:
  1. On launch, compare local VERSION (embedded in the EXE bundle) with the
     VERSION file on the main branch on GitHub.
  2. If newer, download the new NanoBanana.exe from the matching GitHub
     Release asset, save it next to the current EXE as `NanoBanana.new.exe`.
  3. Write a tiny .bat next to the EXE that: waits for the current EXE to
     exit, deletes old EXE, renames new EXE in its place, then launches it.
  4. Spawn that .bat detached, then sys.exit(0) so the EXE handle is released.
  5. The .bat does the swap and starts the fresh EXE. On next launch the
     bundled VERSION inside the new EXE matches the remote → no more prompt.

Why this replaces the old overlay approach:
  - No more <EXE_dir>/user_updates/ directory to manage or migrate
  - VERSION lives in exactly one place: inside the EXE
  - No "last_seen" migration hacks, no path-priority chain
  - Helper script survives after python exits, so we don't need os.execv
"""

import os
import sys
import json
import time
import shutil
import tempfile
import urllib.request

REPO = "productionkhu-tech/freewill-nanobanana"
BRANCH = "main"
REMOTE_VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/VERSION"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases"


def _bundle_version_file():
    """VERSION inside the PyInstaller bundle — the only source of truth."""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "VERSION")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")


def get_current_version():
    try:
        with open(_bundle_version_file(), "r", encoding="utf-8") as f:
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
    has_update = bool(remote) and remote != current and remote > current
    return has_update, current, remote


def _find_release_asset_url(version_tag):
    """Fetch GitHub Release for the given tag, return NanoBanana.exe asset URL."""
    req = urllib.request.Request(
        f"{RELEASES_API}/tags/{version_tag}",
        headers={
            "User-Agent": "NanoBanana-Updater",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    for asset in data.get("assets", []):
        if asset.get("name", "").lower() == "nanobanana.exe":
            return asset.get("browser_download_url", "")
    return ""


def _download(url, dest_path):
    req = urllib.request.Request(url, headers={"User-Agent": "NanoBanana-Updater"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        with open(dest_path, "wb") as f:
            shutil.copyfileobj(resp, f)


def apply_update_and_relaunch(version_tag):
    """Download the new EXE, write a swap-and-relaunch .bat, spawn it, exit.

    Returns True on success (caller should immediately sys.exit(0)).
    Raises on network / download / filesystem errors.
    """
    if not getattr(sys, "frozen", False):
        # Dev mode — not much we can do to "replace" anything.
        raise RuntimeError("Auto-update is only supported on the frozen EXE.")

    exe_path = sys.executable
    exe_dir = os.path.dirname(exe_path)
    exe_name = os.path.basename(exe_path)

    # Find the asset URL for the target version
    url = _find_release_asset_url(version_tag)
    if not url:
        raise RuntimeError(f"No NanoBanana.exe asset found for {version_tag}")

    new_exe_path = os.path.join(exe_dir, "NanoBanana.new.exe")
    print(f"  Downloading {version_tag} EXE...")
    _download(url, new_exe_path)
    size = os.path.getsize(new_exe_path)
    if size < 1_000_000:
        # Something went wrong — don't attempt swap with a tiny file
        try: os.remove(new_exe_path)
        except Exception: pass
        raise RuntimeError(f"Downloaded file too small ({size} bytes)")
    print(f"  Downloaded {size/1024/1024:.1f} MB")

    # Write the swap script into %TEMP% so antivirus doesn't flag a bat
    # sitting next to the EXE.
    swap_bat = os.path.join(
        tempfile.gettempdir(),
        f"nanobanana_update_{int(time.time())}.bat",
    )

    # The .bat:
    #   1. Wait up to ~15s for the old EXE to release its file handle
    #   2. Move NanoBanana.new.exe over NanoBanana.exe (replaces atomically on Windows)
    #   3. Launch the new EXE
    #   4. Delete itself
    bat_body = f"""@echo off
setlocal EnableDelayedExpansion
set "OLD={exe_path}"
set "NEW={new_exe_path}"
set "TRIES=0"
:wait_loop
ren "%OLD%" "{exe_name}.tmp" >nul 2>&1
if errorlevel 1 (
    set /a TRIES+=1
    if !TRIES! GEQ 30 goto :fail
    ping -n 1 127.0.0.1 >nul
    goto :wait_loop
)
ren "%OLD%.tmp" "{exe_name}" >nul 2>&1
del "%OLD%" >nul 2>&1
move /y "%NEW%" "%OLD%" >nul 2>&1
if errorlevel 1 goto :fail
start "" "%OLD%"
goto :cleanup
:fail
REM Couldn't replace — launch the old one so user isn't stuck
if exist "%OLD%" start "" "%OLD%"
:cleanup
(goto) 2>nul & del "%~f0"
"""
    with open(swap_bat, "w", encoding="utf-8") as f:
        f.write(bat_body)

    print(f"  Launching swap script: {swap_bat}")
    # Detached, no console
    DETACHED_PROCESS = 0x00000008
    CREATE_NO_WINDOW = 0x08000000
    import subprocess
    subprocess.Popen(
        ["cmd", "/c", swap_bat],
        creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
        close_fds=True,
    )
    return True


# --- Back-compat helper ---
def run_update_check():
    """Legacy API — the launcher now calls check_for_update / apply_update_and_relaunch
    directly so it can show a Yes/No dialog. This remains only to keep any
    external callers working. Returns False (no silent auto-apply)."""
    has, _, _ = check_for_update()
    return has


# --- One-time cleanup: the old overlay directory ---
def cleanup_legacy_overlay():
    """Earlier releases populated <EXE_dir>/user_updates/ with a partial
    overlay. That directory is obsolete now — nuke it on every launch so
    the old code paths don't resurrect old files."""
    if not getattr(sys, "frozen", False):
        return
    try:
        overlay = os.path.join(os.path.dirname(sys.executable), "user_updates")
        if os.path.isdir(overlay):
            shutil.rmtree(overlay, ignore_errors=True)
            print("  removed legacy user_updates/")
    except Exception as e:
        print(f"  legacy cleanup error: {e}")
