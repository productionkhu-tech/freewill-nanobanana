#!/usr/bin/env python3
"""NanoBanana - macOS launcher (run-from-source local web app).

Windows uses launcher.py + the packaged NanoBanana.exe. macOS instead runs the
Flask app (app.py) directly and opens it in the default browser. This file never
touches the Windows code path: app.py already exposes a headless server via its
`if __name__ == "__main__"` block, so here we just load the user's keys, make
sure the dependencies are present, open the browser, and start Flask.
"""
import os
import sys
import socket
import threading
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
PORT = 5656
URL = "http://%s:%d" % (HOST, PORT)


def load_keys():
    """Load KEY=VALUE lines from keys.env (next to this script) into os.environ.

    Looks for keys.env first next to this app, then in ~/.nanobanana/ (where the
    key installer puts it). Lines beginning with '#' are comments; surrounding
    quotes on values are stripped. A relative GOOGLE_APPLICATION_CREDENTIALS path
    is resolved against the folder that holds keys.env, so the Vertex
    service-account JSON can sit right next to it under either location.
    """
    candidates = [
        os.path.join(HERE, "keys.env"),
        os.path.join(os.path.expanduser("~"), ".nanobanana", "keys.env"),
    ]
    path = next((p for p in candidates if os.path.isfile(p)), None)
    if not path:
        print("[NanoBanana] keys.env not found.")
        print("  -> Put keys.env (and service_account.json) in this folder or in")
        print("     ~/.nanobanana/, or copy keys.env.example to keys.env.")
        return
    base = os.path.dirname(path)
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                os.environ[key] = val
    cred = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if cred and not os.path.isabs(cred):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.join(base, cred)
    print("[NanoBanana] Loaded keys from: " + path)


def _fix_ssl_certs():
    """python.org macOS Python ships with an EMPTY CA bundle until the user
    runs 'Install Certificates.command' - most people skip that step. The SDK
    providers (Gemini/OpenAI/Seedream via httpx) bundle certifi so they still
    work, but Reve and every other plain-urllib call in app.py dies with
    CERTIFICATE_VERIFY_FAILED (seen live 2026-07-21). Point the stdlib ssl
    default-context lookup at certifi's bundle (certifi is always present as
    an openai/httpx dependency). keys.env can override by setting
    SSL_CERT_FILE itself."""
    if os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
        print("[NanoBanana] SSL certificates: using the certifi bundle.")
    except Exception:
        pass


def _missing_deps():
    m = []
    for mod, pip_name in [("flask", "flask"),
                          ("google.genai", "google-genai"),
                          ("PIL", "Pillow")]:
        try:
            __import__(mod)
        except Exception:
            m.append(pip_name)
    return m


def check_deps():
    """On first run, auto-install the required packages (so the user does not
    have to open Terminal and run pip themselves). Tries a normal install, then
    a --user install for PEP 668 (Homebrew) Pythons."""
    if not _missing_deps():
        return
    req = os.path.join(HERE, "requirements_mac.txt")
    print("[NanoBanana] First run: installing required packages (needs internet)...")
    import subprocess
    attempts = [
        [sys.executable, "-m", "pip", "install", "-r", req],
        [sys.executable, "-m", "pip", "install", "--user", "-r", req],
    ]
    for args in attempts:
        try:
            subprocess.run(args, timeout=600)
        except Exception:
            pass
        if not _missing_deps():
            print("[NanoBanana] Packages installed.")
            return
    print("[NanoBanana] Could not install packages automatically.")
    print("  -> Open Terminal in this folder and run:  pip3 install -r requirements_mac.txt")
    sys.exit(1)


def open_browser_when_ready():
    """Poll the port until Flask is accepting connections, then open the browser."""
    for _ in range(60):  # up to ~15s
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                break
        except OSError:
            time.sleep(0.25)
    try:
        webbrowser.open(URL)
    except Exception:
        pass


def auto_update():
    """If this folder is a git clone, fast-forward to the latest main on launch.

    Best-effort and silent on any problem: skips when offline, when this is a
    ZIP download (no .git), or when there are local changes that would block a
    fast-forward. keys.env and the Vertex JSON are gitignored, so they are never
    touched. Set NANOBANANA_AUTO_UPDATE=0 in keys.env to turn this off.
    """
    flag = os.environ.get("NANOBANANA_AUTO_UPDATE", "1").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return
    if not os.path.isdir(os.path.join(HERE, ".git")):
        return  # downloaded as ZIP, not a git clone -> manual updates only
    try:
        import subprocess
        r = subprocess.run(
            ["git", "-C", HERE, "pull", "--ff-only"],
            capture_output=True, text=True, timeout=20,
        )
        out = (r.stdout + " " + r.stderr).strip().lower()
        if r.returncode != 0:
            print("[NanoBanana] Skipped auto-update (using the current version).")
            return
        if "up to date" in out:
            print("[NanoBanana] Already the latest version.")
        else:
            print("[NanoBanana] Updated to the latest version.")
            print("  If anything looks off, run:  pip3 install -r requirements_mac.txt")
    except Exception:
        print("[NanoBanana] Skipped auto-update (offline or git unavailable).")


def main():
    # Python floor guard. The Xcode CommandLineTools python3 is 3.9: pip can
    # only give it google-genai <= 1.47.0, whose ImageConfig has no image_size
    # field, so every Gemini 2K/4K request dies instantly with a cryptic
    # pydantic "extra_forbidden" error (seen live 2026-07-21). Fail fast with
    # instructions instead.
    if sys.version_info < (3, 10):
        print("[NanoBanana] 이 Python(%d.%d)은 너무 오래됐습니다 - 3.10 이상이 필요해요." % sys.version_info[:2])
        print("  (Xcode 명령줄 도구에 딸린 Python이면 Gemini 2K/4K 생성이 동작하지 않습니다)")
        print("  해결:")
        print("   1) https://www.python.org/downloads/macos/ 에서 최신 Python 3 설치")
        print("   2) 설치 후 열리는 폴더에서 'Install Certificates.command' 더블클릭")
        print("   3) 터미널을 완전히 닫고 새로 연 뒤 NanoBanana.command 다시 실행")
        sys.exit(1)
    os.chdir(HERE)
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    # Make the double-click launcher executable so that after this first
    # (Terminal) run, the user can just double-click NanoBanana.command. USB/zip
    # copies often drop the executable bit, so restore it here.
    try:
        cmd_path = os.path.join(HERE, "NanoBanana.command")
        if os.path.isfile(cmd_path) and not os.access(cmd_path, os.X_OK):
            os.chmod(cmd_path, 0o755)
    except Exception:
        pass
    load_keys()
    auto_update()
    check_deps()
    _fix_ssl_certs()
    print("[NanoBanana] Starting on " + URL)
    print("  Keep this Terminal window open while using the app (close it to quit).")
    threading.Thread(target=open_browser_when_ready, daemon=True).start()
    try:
        import app as nb  # Flask app; templates/static resolve next to app.py
    except Exception as e:
        print("[NanoBanana] Failed to import app.py: %s" % e)
        print("  -> Make sure you run this from the NanoBanana folder and that")
        print("     dependencies are installed (pip3 install -r requirements_mac.txt).")
        sys.exit(1)
    threading.Thread(target=nb.init_app, daemon=True).start()
    nb.app.run(host=HOST, port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()
