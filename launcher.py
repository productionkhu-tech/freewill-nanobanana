# -*- coding: utf-8 -*-
"""
NanoBanana Launcher
- Auto-update from GitHub
- Duplicate instance prevention (Mutex + port check)
- System tray integration (pystray)
- Selenium Chrome/Edge app mode
"""

import os
import sys

# PyInstaller: ensure _MEIPASS is in PATH so DLLs like libffi-8.dll can be found
if getattr(sys, 'frozen', False):
    os.environ['PATH'] = sys._MEIPASS + os.pathsep + os.environ.get('PATH', '')

import time
import threading
import subprocess
import socket

# These imports are here so PyInstaller includes them in the frozen bundle.
# They are actually used by app.py which is loaded at runtime.
import flask  # noqa: F401
import jinja2  # noqa: F401
import markupsafe  # noqa: F401
import werkzeug  # noqa: F401
from google import genai  # noqa: F401
from PIL import Image  # noqa: F401

PORT = 5656
APP_URL = f"http://127.0.0.1:{PORT}"
MUTEX_NAME = "NanoBanana_SingleInstance_Mutex"

# PyInstaller: resolve base directory for bundled files
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Global state
tray_icon = None
should_quit = False
mutex_handle = None


# ==========================================
# Duplicate Instance Prevention
# ==========================================
def acquire_mutex():
    """Check duplicate via port binding - no ctypes needed."""
    return not is_port_in_use(PORT)


def is_port_in_use(port):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", port))
        s.close()
        return False
    except OSError:
        return True


def focus_existing_instance():
    """Open the existing instance in browser."""
    import webbrowser
    webbrowser.open(APP_URL)


# ==========================================
# Server
# ==========================================
def wait_for_server(host, port, timeout=30):
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.3)
    return False


def start_flask_server(port):
    from app import app, init_app
    threading.Thread(target=init_app, daemon=True).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)


# ==========================================
# Browser — always opens as standalone app window
# ==========================================
if getattr(sys, 'frozen', False):
    _app_root = os.path.dirname(sys.executable)
else:
    _app_root = os.path.dirname(os.path.abspath(__file__))
APP_PROFILE_DIR = os.path.join(_app_root, ".nb_profile")

_BROWSER_ARGS = [
    "--window-size=1500,920",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-infobars",
    "--disable-application-cache",
    "--disk-cache-size=0",
]

def _find_chrome_or_edge():
    """Find Chrome or Edge executable."""
    candidates = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def open_app_window(url):
    """Open Chrome/Edge in app mode as a standalone window (no tabs, no address bar)."""
    os.makedirs(APP_PROFILE_DIR, exist_ok=True)
    browser = _find_chrome_or_edge()
    if browser:
        cmd = [browser, f"--app={url}", f"--user-data-dir={APP_PROFILE_DIR}"] + _BROWSER_ARGS
        proc = subprocess.Popen(cmd)
        print(f"  Opened: {os.path.basename(browser)} (app mode, PID {proc.pid})")
        return proc
    # Fallback
    import webbrowser
    webbrowser.open(url)
    print("  Opened in default browser (fallback)")
    return None


browser_proc = None


def open_browser():
    global browser_proc
    browser_proc = open_app_window(APP_URL)


def close_browser():
    global browser_proc
    if browser_proc:
        try:
            browser_proc.terminate()
        except Exception:
            pass
        browser_proc = None


# ==========================================
# System Tray
# ==========================================
def create_tray_icon_image():
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (64, 64), (255, 200, 50))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 26)
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), "NB", font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((64 - tw) // 2, (64 - th) // 2 - 2), "NB", fill=(40, 40, 40), font=font)
    return img


def on_tray_open(icon, item):
    global browser_proc
    if browser_proc and browser_proc.poll() is None:
        return  # already open
    threading.Thread(target=open_browser, daemon=True).start()


def on_tray_quit(icon, item):
    global should_quit
    should_quit = True
    close_browser()
    icon.stop()


def setup_tray():
    global tray_icon
    import pystray
    icon_image = create_tray_icon_image()
    menu = pystray.Menu(
        pystray.MenuItem("Open NanoBanana", on_tray_open, default=True),
        pystray.MenuItem("Quit", on_tray_quit),
    )
    tray_icon = pystray.Icon("NanoBanana", icon_image, "NanoBanana", menu)
    tray_icon.run()


# ==========================================
# Main
# ==========================================
def main():
    global should_quit, browser_proc

    print("=" * 50)
    print("  NanoBanana Web - AI Image Studio")
    print("=" * 50)

    # --- Clear Chrome cache on startup ---
    import shutil
    for cache_dir in ["Default/Cache", "Default/Code Cache", "Default/Service Worker"]:
        p = os.path.join(APP_PROFILE_DIR, cache_dir)
        if os.path.isdir(p):
            try:
                shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass

    # --- Duplicate instance check ---
    if not acquire_mutex():
        print("  Already running! Opening existing instance...")
        if is_port_in_use(PORT):
            focus_existing_instance()
        sys.exit(0)

    if is_port_in_use(PORT):
        print(f"  Port {PORT} already in use. Another instance may be running.")
        focus_existing_instance()
        sys.exit(0)

    # --- Auto-update ---
    try:
        from updater import run_update_check
        if run_update_check():
            # Updated - restart process
            os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        print(f"  Update check error: {e}")

    print(f"  Starting server on {APP_URL}")

    # --- Start Flask ---
    server_thread = threading.Thread(target=start_flask_server, args=(PORT,), daemon=True)
    server_thread.start()

    print("  Waiting for server...")
    if not wait_for_server("127.0.0.1", PORT, timeout=15):
        print("  ERROR: Server failed to start!")
        sys.exit(1)
    print("  Server ready!")

    # --- System tray (background thread) ---
    tray_thread = threading.Thread(target=setup_tray, daemon=True)
    tray_thread.start()
    time.sleep(0.5)  # let tray initialize

    # --- Open browser ---
    open_browser()
    print(f"\n  App running at {APP_URL}")
    print("  Close the browser window to minimize to tray.")
    print("  Use tray icon to reopen or quit.\n")

    # --- Main loop ---
    try:
        while not should_quit:
            if browser_proc and browser_proc.poll() is not None:
                # Browser window closed -> minimize to tray
                browser_proc = None
                print("  Browser closed. Running in system tray...")
                if tray_icon:
                    try:
                        tray_icon.notify(
                            "NanoBanana is running in the system tray.\n"
                            "Double-click the tray icon to reopen.",
                            "NanoBanana"
                        )
                    except Exception:
                        pass
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Shutting down...")
        should_quit = True

    # --- Cleanup ---
    close_browser()
    if tray_icon:
        try:
            tray_icon.stop()
        except Exception:
            pass
    print("  Goodbye!")


if __name__ == "__main__":
    main()
