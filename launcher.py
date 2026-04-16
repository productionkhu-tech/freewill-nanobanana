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
import time
import threading
import subprocess
import socket
import ctypes

PORT = 5656
APP_URL = f"http://127.0.0.1:{PORT}"
MUTEX_NAME = "NanoBanana_SingleInstance_Mutex"

# Global state
selenium_driver = None
tray_icon = None
should_quit = False
mutex_handle = None


# ==========================================
# Duplicate Instance Prevention
# ==========================================
def acquire_mutex():
    """Windows named mutex — auto-released on process exit/crash."""
    global mutex_handle
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    mutex_handle = kernel32.CreateMutexW(None, True, MUTEX_NAME)
    last_error = ctypes.get_last_error()
    if last_error == 183:  # ERROR_ALREADY_EXISTS
        return False
    return True


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
    project_dir = os.path.dirname(os.path.abspath(__file__))
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)

    from app import app, init_app
    threading.Thread(target=init_app, daemon=True).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)


# ==========================================
# Browser (Selenium / Subprocess)
# Separate user-data-dir so it opens as a standalone app window,
# not a tab in the user's existing Chrome.
# ==========================================
APP_PROFILE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".chrome_profile")


def open_with_selenium(url):
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options as ChromeOptions

        os.makedirs(APP_PROFILE_DIR, exist_ok=True)

        options = ChromeOptions()
        options.add_argument(f"--app={url}")
        options.add_argument(f"--user-data-dir={APP_PROFILE_DIR}")
        options.add_argument("--window-size=1500,920")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-infobars")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])

        try:
            driver = webdriver.Chrome(options=options)
            print("  Opened in Chrome (app mode)")
            return driver
        except Exception:
            pass

        try:
            from selenium.webdriver.edge.options import Options as EdgeOptions
            edge_opts = EdgeOptions()
            edge_opts.add_argument(f"--app={url}")
            edge_opts.add_argument(f"--user-data-dir={APP_PROFILE_DIR}")
            edge_opts.add_argument("--window-size=1500,920")
            edge_opts.add_argument("--disable-extensions")
            edge_opts.add_argument("--disable-infobars")
            edge_opts.add_argument("--no-first-run")
            edge_opts.add_argument("--no-default-browser-check")
            edge_opts.add_experimental_option("excludeSwitches", ["enable-automation"])
            driver = webdriver.Edge(options=edge_opts)
            print("  Opened in Edge (app mode)")
            return driver
        except Exception:
            pass

    except ImportError:
        pass
    return None


def open_with_subprocess(url):
    os.makedirs(APP_PROFILE_DIR, exist_ok=True)
    chrome_paths = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    edge_paths = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for path in chrome_paths + edge_paths:
        if os.path.exists(path):
            try:
                subprocess.Popen([
                    path, f"--app={url}",
                    f"--user-data-dir={APP_PROFILE_DIR}",
                    "--window-size=1500,920",
                    "--no-first-run",
                    "--no-default-browser-check",
                ])
                print(f"  Opened with: {os.path.basename(path)} (app mode)")
                return True
            except Exception:
                continue
    import webbrowser
    webbrowser.open(url)
    print("  Opened in default browser")
    return True


def open_browser():
    global selenium_driver
    driver = open_with_selenium(APP_URL)
    if driver:
        selenium_driver = driver
    else:
        open_with_subprocess(APP_URL)


def close_browser():
    global selenium_driver
    if selenium_driver:
        try:
            selenium_driver.quit()
        except Exception:
            pass
        selenium_driver = None


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
    global selenium_driver
    if selenium_driver:
        try:
            _ = selenium_driver.title
            return  # already open
        except Exception:
            selenium_driver = None
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
    global should_quit

    print("=" * 50)
    print("  NanoBanana Web — AI Image Studio")
    print("=" * 50)

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
            # Updated — restart process
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
            if selenium_driver:
                try:
                    _ = selenium_driver.title
                    time.sleep(1)
                except Exception:
                    # Browser window closed → minimize to tray
                    selenium_driver = None
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
            else:
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
