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
import re
import sys
import json
import time
import hashlib
import shutil
import tempfile
import urllib.request

REPO = "productionkhu-tech/freewill-nanobanana"
BRANCH = "main"
REMOTE_VERSION_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/VERSION"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases"


def _version_tuple(v):
    """Turn a version string into a tuple of ints for robust comparison.
    String comparison of 'v2026-04-17' > 'v2026-04-9' happens to work today
    only because every segment is zero-padded. If the format ever shifts
    (e.g. a 3-digit NN suffix) string compare quietly breaks; tuple compare
    keeps doing the right thing."""
    nums = re.findall(r"\d+", v or "")
    try:
        return tuple(int(n) for n in nums)
    except ValueError:
        return tuple()


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
    if not remote or remote == current:
        return False, current, remote
    cur_t = _version_tuple(current)
    rem_t = _version_tuple(remote)
    has_update = bool(rem_t) and rem_t > cur_t
    return has_update, current, remote


def _find_release_assets(version_tag):
    """Return (exe_url, expected_sha256) for the release at `version_tag`.
    If the release body contains a line like "sha256: <hex>" or there's a
    NanoBanana.exe.sha256 asset, that hash is returned and verified after
    download. Otherwise returns empty sha — size-only validation."""
    req = urllib.request.Request(
        f"{RELEASES_API}/tags/{version_tag}",
        headers={
            "User-Agent": "NanoBanana-Updater",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    exe_url = ""
    sha_url = ""
    for asset in data.get("assets", []):
        name = asset.get("name", "").lower()
        if name == "nanobanana.exe":
            exe_url = asset.get("browser_download_url", "")
        elif name == "nanobanana.exe.sha256":
            sha_url = asset.get("browser_download_url", "")
    # Inline sha256 in release body (preferred, no second asset needed)
    body = (data.get("body") or "")
    m = re.search(r"(?:sha-?256|hash)\s*[:=]\s*([0-9a-fA-F]{64})", body, re.IGNORECASE)
    inline_sha = m.group(1).lower() if m else ""
    # Fetch sidecar sha256 file if present
    remote_sha = ""
    if sha_url:
        try:
            with urllib.request.urlopen(
                urllib.request.Request(sha_url, headers={"User-Agent": "NanoBanana-Updater"}),
                timeout=10,
            ) as r:
                txt = r.read().decode("utf-8", errors="replace").strip().split()
                if txt and re.fullmatch(r"[0-9a-fA-F]{64}", txt[0]):
                    remote_sha = txt[0].lower()
        except Exception:
            pass
    return exe_url, (inline_sha or remote_sha)


def _download_with_retry(url, dest_path, attempts=3):
    """Stream download with a short retry loop. A truncated mid-stream
    download would otherwise pass the size check but crash on launch."""
    last_err = None
    for i in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NanoBanana-Updater"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                with open(dest_path, "wb") as f:
                    shutil.copyfileobj(resp, f)
            got = os.path.getsize(dest_path)
            if total and got != total:
                raise IOError(f"Short read: got {got}, expected {total}")
            return
        except Exception as e:
            last_err = e
            print(f"  download attempt {i+1}/{attempts} failed: {e}")
            try:
                os.remove(dest_path)
            except Exception:
                pass
            time.sleep(min(2 ** i, 5))
    raise last_err if last_err else IOError("download failed")


def _sha256_of(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


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

    # If the EXE lives under Program Files we'd need UAC to overwrite it.
    # Surface that clearly so the user can move the install elsewhere.
    pf1 = os.environ.get("ProgramFiles", "")
    pf2 = os.environ.get("ProgramFiles(x86)", "")
    exe_real = os.path.realpath(exe_path)
    for root in (pf1, pf2):
        if root and os.path.commonpath([exe_real, os.path.realpath(root)]) == os.path.realpath(root):
            raise RuntimeError(
                "Program Files에 설치되어 있어 자동 업데이트를 적용할 수 없습니다.\n"
                "바탕화면 같은 일반 폴더로 NanoBanana.exe를 옮긴 뒤 다시 실행해주세요."
            )

    url, expected_sha = _find_release_assets(version_tag)
    if not url:
        raise RuntimeError(f"No NanoBanana.exe asset found for {version_tag}")

    new_exe_path = os.path.join(exe_dir, "NanoBanana.new.exe")
    print(f"  Downloading {version_tag} EXE...")
    _download_with_retry(url, new_exe_path)
    size = os.path.getsize(new_exe_path)
    if size < 1_000_000:
        try: os.remove(new_exe_path)
        except Exception: pass
        raise RuntimeError(f"Downloaded file too small ({size} bytes)")
    # Integrity check — if the release publishes a sha256 (inline in body or
    # sidecar asset) we verify here. A mismatch aborts the swap so we never
    # replace a working EXE with a corrupted download.
    if expected_sha:
        got_sha = _sha256_of(new_exe_path)
        if got_sha.lower() != expected_sha.lower():
            try: os.remove(new_exe_path)
            except Exception: pass
            raise RuntimeError(
                f"Hash mismatch: expected {expected_sha[:12]}..., got {got_sha[:12]}..."
            )
        print(f"  SHA256 verified")
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
    # Pass our PID to the bat so it can force-kill the lingering process if
    # the polite exit didn't release the EXE handle.
    #
    # KEY FIX vs v1723 and earlier: `chcp 65001` at the top of the bat.
    # On Korean Windows the console default codepage is 949 (EUC-KR). When
    # cmd.exe reads a UTF-8 encoded .bat file in cp949 mode, any Korean
    # character in a path like "C:\Users\...\나노바나나 api\NanoBanana.exe"
    # gets mangled into garbage bytes. Every `ren`/`move`/`start` then
    # silently fails because the mangled path doesn't exist, and the bat
    # falls through to :fail with no diagnostic. Switching to UTF-8
    # codepage BEFORE the path variables are expanded fixes this.
    #
    # Also writes a log to %TEMP%\nanobanana_update.log so future failures
    # are debuggable instead of mysterious.
    our_pid = os.getpid()
    bat_body = f"""@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
set "OLD={exe_path}"
set "NEW={new_exe_path}"
set "PID={our_pid}"
set "BACKUP=%OLD%.old"
set "LOG=%TEMP%\\nanobanana_update.log"

echo ==== %DATE% %TIME% ==== >> "%LOG%"
echo OLD="%OLD%" >> "%LOG%"
echo NEW="%NEW%" >> "%LOG%"
echo PID=%PID% >> "%LOG%"

set "TRIES=0"
:wait_loop
REM Probe: if the running EXE releases its file handle, rename succeeds.
ren "%OLD%" "{exe_name}.old" >nul 2>&1
if not errorlevel 1 goto :do_move
set /a TRIES+=1
echo wait_loop try=!TRIES! rc=%errorlevel% >> "%LOG%"
if !TRIES! GEQ 6 goto :force_kill
ping -n 2 127.0.0.1 >nul
goto :wait_loop

:force_kill
echo force_kill PID=%PID% >> "%LOG%"
taskkill /F /PID %PID% >nul 2>&1
ping -n 2 127.0.0.1 >nul
ren "%OLD%" "{exe_name}.old" >nul 2>&1
if not errorlevel 1 goto :do_move
echo taskkill by name (last resort) >> "%LOG%"
taskkill /F /IM "{exe_name}" >nul 2>&1
ping -n 2 127.0.0.1 >nul
ren "%OLD%" "{exe_name}.old" >nul 2>&1
if errorlevel 1 (
    echo rename still failing after both taskkills >> "%LOG%"
    goto :fail
)

:do_move
REM OLD has been renamed to OLD.old; the OLD position is empty.
REM Move NEW into that position (disk-move, not copy, so it's atomic on same volume).
echo moving "%NEW%" -^> "%OLD%" >> "%LOG%"
move /y "%NEW%" "%OLD%" >nul 2>&1
if errorlevel 1 (
    echo move failed rc=%errorlevel%, restoring backup >> "%LOG%"
    if exist "%BACKUP%" ren "%BACKUP%" "{exe_name}" >nul 2>&1
    goto :fail
)
echo move ok, cleaning backup >> "%LOG%"
REM Force-delete the backup. If Windows is still holding a reference to
REM the renamed-away file (rare but happens on some AV configs), wait a
REM beat and retry so we don't leave NanoBanana.exe.old orphaned in the
REM user's folder forever.
del /F /Q "%BACKUP%" >nul 2>&1
if exist "%BACKUP%" (
    echo backup still present, retrying after 1s >> "%LOG%"
    ping -n 2 127.0.0.1 >nul
    del /F /Q "%BACKUP%" >nul 2>&1
)
if exist "%BACKUP%" echo WARN backup could not be removed >> "%LOG%"
echo launching new EXE >> "%LOG%"
start "" "%OLD%"
echo SUCCESS >> "%LOG%"
goto :cleanup

:fail
echo FAIL >> "%LOG%"
REM Try to leave the user SOMETHING runnable.
if exist "%OLD%" start "" "%OLD%" & goto :cleanup
if exist "%BACKUP%" (
    ren "%BACKUP%" "{exe_name}" >nul 2>&1
    if exist "%OLD%" start "" "%OLD%"
)

:cleanup
(goto) 2>nul & del "%~f0"
"""
    # No BOM — cmd's `chcp 65001` directive at the top switches the
    # codepage BEFORE the variable assignments are parsed. Writing with
    # utf-8-sig would put a BOM at byte 0 which cmd treats as garbage
    # before even seeing @echo off.
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
