# -*- coding: utf-8 -*-
"""
NanoBanana Launcher - Native window via pywebview
"""

import os
import sys

if getattr(sys, 'frozen', False):
    os.environ['PATH'] = sys._MEIPASS + os.pathsep + os.environ.get('PATH', '')

import time
import threading
import socket

# PyInstaller: force-include these so app.py can import them
import flask  # noqa: F401
import jinja2  # noqa: F401
import markupsafe  # noqa: F401
import werkzeug  # noqa: F401
from google import genai  # noqa: F401
from PIL import Image  # noqa: F401
import webview  # noqa: F401

PORT = 5656
APP_URL = f"http://127.0.0.1:{PORT}"

if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def is_port_in_use(port):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", port))
        s.close()
        return False
    except OSError:
        return True


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


def main():
    print("=" * 50)
    print("  NanoBanana - AI Image Studio")
    print("=" * 50)

    # Duplicate check
    if is_port_in_use(PORT):
        print("  Already running!")
        import webbrowser
        webbrowser.open(APP_URL)
        sys.exit(0)

    # Auto-update
    try:
        from updater import run_update_check
        if run_update_check():
            os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        print(f"  Update check: {e}")

    # Start Flask server
    print(f"  Starting server on {APP_URL}")
    server_thread = threading.Thread(target=start_flask_server, args=(PORT,), daemon=True)
    server_thread.start()

    print("  Waiting for server...")
    if not wait_for_server("127.0.0.1", PORT, timeout=15):
        print("  ERROR: Server failed to start!")
        sys.exit(1)
    print("  Server ready!")

    # Open native window with pywebview
    print("  Opening NanoBanana window...")
    webview.create_window(
        "NanoBanana",
        APP_URL,
        width=1500,
        height=920,
        min_size=(1000, 600),
    )
    webview.start()
    print("  Goodbye!")


if __name__ == "__main__":
    main()
