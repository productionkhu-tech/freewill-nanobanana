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

# PyInstaller: force-include
import flask  # noqa: F401
import jinja2  # noqa: F401
import markupsafe  # noqa: F401
import werkzeug  # noqa: F401
from google import genai  # noqa: F401
from PIL import Image  # noqa: F401
import webview  # noqa: F401

# GPU flags for crisper text
os.environ.setdefault(
    "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
    "--enable-gpu-rasterization --enable-zero-copy",
)

# DPI awareness so Windows scaling is respected
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

PORT = 5656
APP_URL = f"http://127.0.0.1:{PORT}"

if getattr(sys, 'frozen', False):
    BUNDLE_DIR = sys._MEIPASS
    EXE_DIR = os.path.dirname(sys.executable)
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    EXE_DIR = BUNDLE_DIR

BASE_DIR = BUNDLE_DIR
ICON_PATH = os.path.join(BASE_DIR, "app.ico")
if not os.path.isfile(ICON_PATH):
    ICON_PATH = None

# Clean up any leftover overlay dir from older versions. The updater no
# longer uses this approach — we swap the whole EXE instead.
try:
    from updater import cleanup_legacy_overlay
    cleanup_legacy_overlay()
except Exception:
    pass


# --- Close flow state ---
_force_close = False
_window = None


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


def show_error_and_exit(title, message):
    """Show an error messagebox and exit."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)  # MB_ICONERROR
    except Exception:
        print(f"ERROR: {title}\n{message}")
    sys.exit(1)


def check_api_env():
    """Verify required API credentials are present; abort with dialog if not."""
    studio_key = os.environ.get("NANOBANANA_STUDIO_KEY", "").strip()
    vertex_creds = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    vertex_project = os.environ.get("NANOBANANA_PROJECT_ID", "").strip()

    has_studio = bool(studio_key)
    has_vertex = bool(vertex_creds) and os.path.isfile(vertex_creds) and bool(vertex_project)

    if not (has_studio or has_vertex):
        show_error_and_exit(
            "NanoBanana - API 자격증명 없음",
            "API 환경변수가 설정되지 않아 앱을 시작할 수 없습니다.\n\n"
            "필요한 환경변수 (하나 이상):\n"
            "  • NANOBANANA_STUDIO_KEY  (Google AI Studio)\n"
            "  • GOOGLE_APPLICATION_CREDENTIALS + NANOBANANA_PROJECT_ID  (Vertex AI)\n\n"
            "배포 패키지에 포함된 setup_env.bat 을 관리자 권한으로 실행한 후\n"
            "컴퓨터를 재시작하거나 새 터미널/탐색기 세션을 열어주세요."
        )


class JsApi:
    """Bridge exposed to JavaScript via window.pywebview.api"""
    def force_close(self):
        global _force_close, _window
        print("  JS -> force_close()")
        _force_close = True
        try:
            from app import cleanup as app_cleanup
            app_cleanup()
        except Exception as e:
            print(f"  cleanup error: {e}")
        if _window is not None:
            try:
                _window.destroy()
            except Exception as e:
                print(f"  destroy error: {e}")

    def cleanup_temp(self):
        try:
            from app import cleanup as app_cleanup
            app_cleanup()
        except Exception as e:
            print(f"  cleanup_temp error: {e}")

    def open_viewer(self, filepath):
        """Open image viewer in new pywebview window (dedupe by filepath)."""
        print(f"  JS -> open_viewer({filepath!r})")
        try:
            import urllib.parse
            for w in list(webview.windows):
                if w is _window:
                    continue
                if getattr(w, "_nb_filepath", None) == filepath:
                    try:
                        w.show()
                        w.restore()
                    except Exception:
                        pass
                    return
            encoded = urllib.parse.quote(filepath, safe="")
            viewer_url = f"{APP_URL}/viewer?path={encoded}"
            title = os.path.basename(filepath) or "Image Viewer"
            kwargs = dict(
                title=title, url=viewer_url,
                width=1200, height=800,
                min_size=(600, 400),
                resizable=True,
            )
            new_win = webview.create_window(**kwargs)
            try:
                new_win._nb_filepath = filepath
            except Exception:
                pass
        except Exception as e:
            print(f"  open_viewer error: {e}")

    def open_prompt_popup(self, prompt, filename):
        """Open prompt text in a new pywebview window."""
        try:
            import urllib.parse, base64
            b64 = base64.b64encode((prompt or "").encode("utf-8")).decode("ascii")
            safe_name = urllib.parse.quote(filename or "prompt", safe="")
            url = f"{APP_URL}/prompt-popup?b64={b64}&name={safe_name}"
            webview.create_window(
                title=f"Prompt — {filename or ''}",
                url=url,
                width=600, height=420,
                resizable=True,
            )
        except Exception as e:
            print(f"  open_prompt_popup error: {e}")


def _set_close_requested_flag():
    try:
        from app import state
        state.close_requested = True
    except Exception as e:
        print(f"  flag set error: {e}")


def on_closing():
    global _force_close
    print(f"  on_closing fired, force={_force_close}")
    if _force_close:
        return True
    _set_close_requested_flag()
    return False


def start_flask_server(port):
    from app import app, init_app
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    threading.Thread(target=init_app, daemon=True).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True, use_reloader=False)


def main():
    global _window
    print("=" * 50)
    print("  NanoBanana - AI Image Studio")
    print("=" * 50)

    # Verify API credentials — abort if missing
    check_api_env()

    # Single-instance guard
    if is_port_in_use(PORT):
        print("  Port busy - killing old instance...")
        if sys.platform == "win32":
            os.system("taskkill /F /IM NanoBanana.exe >nul 2>&1")
            time.sleep(2)
        if is_port_in_use(PORT):
            show_error_and_exit(
                "NanoBanana",
                f"포트 {PORT}가 사용 중입니다. 다른 프로그램을 종료하고 다시 실행해주세요."
            )

    # Auto-update — Yes/No dialog, user decides. Whole-EXE swap on accept.
    try:
        from updater import check_for_update, apply_update_and_relaunch
        has_update, current, remote = check_for_update()
        print(f"  Local={current}  Remote={remote}  HasUpdate={has_update}")
        if has_update:
            try:
                import ctypes
                MB_YESNO = 0x04
                MB_ICONINFO = 0x40
                MB_TOPMOST = 0x00040000
                IDYES = 6
                msg = (
                    f"새 버전이 있어요!\n\n"
                    f"현재 버전: {current}\n"
                    f"최신 버전: {remote}\n\n"
                    f"지금 업데이트하시겠습니까?\n"
                    f"(앱이 자동으로 재시작됩니다)"
                )
                result = ctypes.windll.user32.MessageBoxW(
                    0, msg, "NanoBanana 업데이트",
                    MB_YESNO | MB_ICONINFO | MB_TOPMOST
                )
                if result == IDYES:
                    print("  User accepted update — downloading...")
                    try:
                        apply_update_and_relaunch(remote)
                        # A swap script is now running in a detached cmd.
                        # Exit this process so the script can rename the EXE.
                        print("  Swap script launched, exiting...")
                        sys.exit(0)
                    except Exception as e:
                        ctypes.windll.user32.MessageBoxW(
                            0, f"업데이트 실패:\n{e}\n\n현재 버전으로 계속 진행합니다.",
                            "NanoBanana", 0x10
                        )
                else:
                    print("  User skipped update")
            except Exception as e:
                print(f"  Update prompt error: {e}")
    except Exception as e:
        print(f"  Update check: {e}")

    # Start Flask server
    print(f"  Starting server on {APP_URL}")
    threading.Thread(target=start_flask_server, args=(PORT,), daemon=True).start()

    print("  Waiting for server...")
    if not wait_for_server("127.0.0.1", PORT, timeout=15):
        show_error_and_exit("NanoBanana", "서버가 시작되지 않았습니다.")
    print("  Server ready!")

    print("  Opening window...")
    js_api = JsApi()
    window_kwargs = dict(
        title="NanoBanana",
        url=APP_URL,
        width=1500, height=920,
        min_size=(1000, 600),
        js_api=js_api,
    )
    _window = webview.create_window(**window_kwargs)
    _window.events.closing += on_closing

    # Force the pywebview window + taskbar icon on Windows.
    # `webview.start(icon=...)` is unreliable on WebView2; use Win32 instead.
    def _set_window_icon():
        if sys.platform != "win32" or not ICON_PATH:
            return
        try:
            import ctypes
            # Set AppUserModelID so Windows groups windows under our icon
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "NanoBanana.AIImageStudio"
            )
            # Find and set icon on the window handle
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x00000010
            LR_DEFAULTSIZE = 0x00000040
            WM_SETICON = 0x0080
            ICON_SMALL = 0
            ICON_BIG = 1

            user32 = ctypes.windll.user32
            # Load large (32x32) and small (16x16) icons
            hicon_big = user32.LoadImageW(
                None, ICON_PATH, IMAGE_ICON, 32, 32,
                LR_LOADFROMFILE | LR_DEFAULTSIZE
            )
            hicon_small = user32.LoadImageW(
                None, ICON_PATH, IMAGE_ICON, 16, 16,
                LR_LOADFROMFILE | LR_DEFAULTSIZE
            )

            def apply():
                try:
                    # Find our window by title
                    hwnd = user32.FindWindowW(None, "NanoBanana")
                    if hwnd:
                        if hicon_small:
                            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
                        if hicon_big:
                            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
                        return True
                except Exception as e:
                    print(f"  icon apply error: {e}")
                return False

            # Retry for up to 5s since window may not exist yet
            def retry_loop():
                for _ in range(50):
                    if apply():
                        print("  Window icon set")
                        return
                    time.sleep(0.1)
                print("  Could not find window to set icon")

            threading.Thread(target=retry_loop, daemon=True).start()
        except Exception as e:
            print(f"  icon setup error: {e}")

    _set_window_icon()
    webview.start(icon=ICON_PATH if ICON_PATH else None)
    print("  Goodbye!")


if __name__ == "__main__":
    main()
