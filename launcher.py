# -*- coding: utf-8 -*-
"""
NanoBanana Launcher - Native window via pywebview
"""

import os
import sys

# ============================================================
# CRITICAL: make print() unkillable on Korean Windows BEFORE anything
# else imports. Two layers:
#
#   1. Reconfigure sys.stdout/stderr to UTF-8. On a --windowed PyInstaller
#      build the streams can still be cp949 or NoneType even after this
#      (the bootloader initializes them oddly), so we try reconfigure, then
#      fall back to wrapping .buffer with io.TextIOWrapper, then finally
#      redirecting to os.devnull.
#   2. Wrap builtins.print with a try/except that silently swallows
#      UnicodeEncodeError. This is the belt under the reconfigure suspenders
#      — even if step 1 somehow fails on a user's machine, a log string
#      containing an em-dash or curly quote will no longer crash the app.
#
# Pre-v1722 builds crashed here with "'cp949' codec can't encode '\u2014'"
# whenever the single-instance mutex caught a second launch, because the
# "Another instance is already running —" print contained U+2014.
# ============================================================

def _fix_std_stream(attr):
    s = getattr(sys, attr, None)
    # First choice: reconfigure the existing stream.
    try:
        if s is not None and hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
            return
    except Exception:
        pass
    # Second choice: rewrap the binary buffer as UTF-8 text.
    try:
        import io as _io
        if s is not None and getattr(s, "buffer", None) is not None:
            setattr(sys, attr, _io.TextIOWrapper(
                s.buffer, encoding="utf-8", errors="replace", line_buffering=True))
            return
    except Exception:
        pass
    # Last resort: send the output into a hole. Any cp949 path is dead.
    try:
        setattr(sys, attr, open(os.devnull, "w", encoding="utf-8", errors="replace"))
    except Exception:
        pass

_fix_std_stream("stdout")
_fix_std_stream("stderr")

# Belt under the suspenders: wrap print so UnicodeEncodeError can never
# propagate out. Worst case we drop the log line; we never crash the app
# because of a debug message.
import builtins as _builtins
_real_print = _builtins.print
def _safe_print(*args, **kwargs):
    try:
        _real_print(*args, **kwargs)
    except UnicodeEncodeError:
        try:
            safe = [str(a).encode("ascii", "replace").decode("ascii") for a in args]
            _real_print(*safe, **kwargs)
        except Exception:
            pass
    except Exception:
        # Any other I/O error on stdout (closed handle, broken pipe) — ignore.
        pass
_builtins.print = _safe_print

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

# Clean up stale NanoBanana.new.exe / NanoBanana.exe.old orphans left
# behind by a failed or interrupted auto-update swap. These files sit
# next to the real EXE and are pure dead weight - the user sees them,
# asks what they are, and we have to apologize. We only delete our own
# named orphans, never anything else in the folder.
try:
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        _exe_dir = os.path.dirname(sys.executable)
        for _name in ("NanoBanana.new.exe", "NanoBanana.exe.old"):
            _orphan = os.path.join(_exe_dir, _name)
            if os.path.isfile(_orphan):
                try:
                    os.remove(_orphan)
                except Exception:
                    pass
except Exception as _e:
    print(f"  orphan cleanup: {_e}")

# NOTE: _MEI* temp folder sweep was moved OUT of module-init into a
# deferred background thread (see _sweep_stale_mei below). Running shutil
# operations on temp at the exact moment PyInstaller is still finishing
# its own runtime extraction caused intermittent ModuleNotFoundError
# crashes on launch (imports like `_socket` failed because the sweep was
# racing the extractor). The sweep is pure housekeeping — it doesn't need
# to happen before the app starts.
_sweep_stale_mei_done = False
def _sweep_stale_mei():
    global _sweep_stale_mei_done
    if _sweep_stale_mei_done:
        return
    _sweep_stale_mei_done = True
    try:
        if not (getattr(sys, "frozen", False) and sys.platform == "win32"):
            return
        import tempfile as _tempfile
        import shutil as _shutil
        import re as _re
        _temp = _tempfile.gettempdir()
        _our_mei = os.path.normcase(getattr(sys, "_MEIPASS", "") or "")
        if not _our_mei:
            return  # belt-and-suspenders: never sweep when we can't identify ourselves
        for _name in os.listdir(_temp):
            if not _re.match(r"^_MEI[0-9a-fA-F]+$", _name):
                continue
            _path = os.path.join(_temp, _name)
            if os.path.normcase(_path) == _our_mei:
                continue
            if not os.path.isdir(_path):
                continue
            try:
                _shutil.rmtree(_path, ignore_errors=True)
            except Exception:
                pass
    except Exception as _e:
        print(f"  _MEI sweep: {_e}")


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


# --- Single-instance guard via a named Win32 mutex.
# If another NanoBanana is already running, focus its window and exit silently.
_instance_mutex_handle = None

def _focus_existing_nanobanana_window():
    """Bring any already-running NanoBanana window to the front.

    Previously we used FindWindowW(None, "NanoBanana") which requires an
    EXACT title match. The app's JS mutates the title as the project
    loads ("NanoBanana - project.json *" with a dirty marker), so the
    exact match missed the window and the user saw nothing happen on a
    second launch. We now EnumWindows and match any visible top-level
    window whose title starts with "NanoBanana".
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        found_hwnd = [0]

        def _cb(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value.startswith("NanoBanana"):
                    found_hwnd[0] = hwnd
                    return False  # stop enumeration
            except Exception:
                pass
            return True

        user32.EnumWindows(EnumWindowsProc(_cb), 0)
        hwnd = found_hwnd[0]
        if not hwnd:
            return False

        # SetForegroundWindow has input-rules restrictions when called from
        # a non-foreground process. AllowSetForegroundWindow(ASFW_ANY)
        # lifts the restriction for this call; combined with a restore-
        # then-foreground sequence this reliably pops the existing window.
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        try:
            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # ASFW_ANY
        except Exception:
            pass
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception as e:
        print(f"  focus existing window failed: {e}")
        return False


def acquire_single_instance():
    global _instance_mutex_handle
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes
        ERROR_ALREADY_EXISTS = 183
        # CRITICAL: use_last_error=True + ctypes.get_last_error() is the
        # only reliable way to read Windows' last-error from ctypes. The
        # default ctypes.windll.kernel32 doesn't preserve GetLastError()
        # across the Python<->C boundary, so ERROR_ALREADY_EXISTS could
        # come back as 0 and we'd treat a second launch as the first.
        # That's what caused the "two windows + port-5656-in-use error"
        # symptom on a second double-click.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        _instance_mutex_handle = kernel32.CreateMutexW(
            None, False, "NanoBanana-AIImageStudio-SingleInstance-v1"
        )
        err = ctypes.get_last_error()
        if err == ERROR_ALREADY_EXISTS:
            _focus_existing_nanobanana_window()
            return False
        return True
    except Exception as e:
        print(f"  single-instance check failed: {e}")
        return True  # don't block launch on Win32 errors


def check_webview2_installed():
    """WebView2 Evergreen Runtime is required on Win10. Detect via registry
    (matches Microsoft's own recommended check). Returns True if installed."""
    if sys.platform != "win32":
        return True
    try:
        import winreg
        paths = [
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
            (winreg.HKEY_CURRENT_USER,
             r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        ]
        for hive, path in paths:
            try:
                with winreg.OpenKey(hive, path) as k:
                    v, _ = winreg.QueryValueEx(k, "pv")
                    if v and v != "0.0.0.0":
                        return True
            except OSError:
                continue
        return False
    except Exception:
        return True  # if we can't tell, assume OK


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
        """Open image viewer in new pywebview window (dedupe by filepath).
        Skips over windows that have already been destroyed — pywebview doesn't
        always remove them from its windows list, so we test each one."""
        print(f"  JS -> open_viewer({filepath!r})")
        try:
            import urllib.parse
            for w in list(webview.windows):
                if w is _window:
                    continue
                # If the handle is gone (user closed the viewer), skip it.
                try:
                    if getattr(w, "_nb_filepath", None) != filepath:
                        continue
                    w.show()
                    w.restore()
                    return
                except Exception:
                    # Window is dead — move on and open a fresh one.
                    continue
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
                title=f"Prompt - {filename or ''}",
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

    # Single-instance guard - silently focus existing window if one is running.
    # NOTE: keep the log strings ASCII-only. On Korean Windows a --windowed
    # PyInstaller build can leave sys.stdout stuck on cp949 even after we
    # reconfigure, and a single em-dash in this print used to crash the
    # EXE before main() could hand off to the webview window.
    if not acquire_single_instance():
        print("  Another instance is already running - focused and exiting.")
        sys.exit(0)

    # Verify API credentials — abort if missing
    check_api_env()

    # Warn (don't abort) if installed under Program Files. The auto-updater
    # can't overwrite the EXE there without UAC, so a user stuck there would
    # silently stay on an old version forever.
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        try:
            exe_real = os.path.realpath(sys.executable)
            for root in (os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", "")):
                if not root:
                    continue
                try:
                    if os.path.commonpath([exe_real, os.path.realpath(root)]) == os.path.realpath(root):
                        import ctypes
                        ctypes.windll.user32.MessageBoxW(
                            0,
                            "NanoBanana이 Program Files에 설치되어 있어요.\n\n"
                            "이 위치에서는 자동 업데이트가 작동하지 않습니다.\n"
                            "바탕화면 같은 일반 폴더로 EXE를 옮긴 뒤 다시 실행해주세요.",
                            "NanoBanana", 0x30,
                        )
                        break
                except ValueError:
                    continue
        except Exception as e:
            print(f"  Program Files check: {e}")

    # WebView2 presence check — friendly message if absent.
    if not check_webview2_installed():
        try:
            import ctypes
            MB_OK = 0x00
            MB_ICONWARN = 0x30
            result = ctypes.windll.user32.MessageBoxW(
                0,
                "Microsoft Edge WebView2 런타임이 필요합니다.\n\n"
                "https://go.microsoft.com/fwlink/p/?LinkId=2124703 에서\n"
                "다운로드해 설치한 뒤 앱을 다시 실행해주세요.",
                "NanoBanana",
                MB_OK | MB_ICONWARN,
            )
        except Exception:
            pass
        sys.exit(1)

    # Port collision fallback. If the single-instance mutex check somehow
    # missed (ctypes GetLastError quirk, session isolation, etc.), the port
    # being in use is an equally valid signal that another NanoBanana is
    # running. Focus its window and exit quietly instead of showing a
    # scary "port 5656 in use" error to the user.
    if is_port_in_use(PORT):
        print("  Port busy - assuming another instance is running; focusing it.")
        try:
            _focus_existing_nanobanana_window()
        except Exception:
            pass
        sys.exit(0)

    # Async update check — does NOT block the window from opening. A popup
    # shows later if an update is available. Fixes the "8s black screen"
    # startup delay on flaky networks.
    def _bg_update_check():
        # Run the _MEI sweep AFTER the main Python runtime has had time to
        # settle. Running it at module-init was racing with the extractor
        # and caused occasional ModuleNotFoundError on launch.
        try:
            _sweep_stale_mei()
        except Exception:
            pass
        try:
            time.sleep(2)  # let the UI settle first
            from updater import check_for_update, apply_update_and_relaunch
            has_update, current, remote = check_for_update()
            print(f"  Local={current}  Remote={remote}  HasUpdate={has_update}")
            # Mirror the result into the in-app log panel AND as a toast the
            # first time we check, so a user who doesn't see the modal popup
            # can still see whether we checked, succeeded, or failed.
            try:
                from app import state
                if not remote:
                    msg = f"Update check failed (local {current}) - network blocked?"
                elif remote == current:
                    msg = f"Already on latest version ({current})"
                elif has_update:
                    msg = f"Update available: {current} -> {remote}"
                else:
                    msg = f"Local {current} >= remote {remote} (no update)"
                state.log(msg)
                # Also push a toast event so the frontend can show it
                state.push_event({"type": "update_status", "message": msg, "has_update": bool(has_update)})
            except Exception:
                pass
            if not has_update:
                return
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
                # Immediately show a full-screen "installing" overlay in the
                # running app so the user isn't staring at a normal UI while
                # we download 33MB in the background. Previously the click
                # led to ~10s of visible silence, which read as "update
                # didn't work, let me relaunch" — exactly the opposite of
                # what we want.
                if _window is not None:
                    try:
                        _window.evaluate_js("""
                            (function(){
                                var o=document.createElement('div');
                                o.id='nbUpdateOverlay';
                                o.style.cssText='position:fixed;inset:0;background:rgba(12,12,14,0.96);z-index:99999;display:flex;align-items:center;justify-content:center;color:#F5F5F7;font-family:Malgun Gothic,Segoe UI,sans-serif';
                                o.innerHTML='<div style=\"text-align:center;padding:40px\"><div style=\"font-size:22px;font-weight:600;margin-bottom:14px\">업데이트 설치 중…</div><div style=\"font-size:13px;color:#A1A1A6;line-height:1.7\">새 버전을 다운로드하고 있어요.<br>잠시 후 앱이 자동으로 다시 열립니다.</div><div style=\"margin-top:22px;width:220px;height:3px;background:#2C2C2E;border-radius:999px;overflow:hidden\"><div style=\"height:100%;background:#D4A574;animation:nbp 1.2s ease-in-out infinite\"></div></div></div><style>@keyframes nbp{0%{width:0;margin-left:0}50%{width:100%;margin-left:0}100%{width:0;margin-left:100%}}</style>';
                                document.body.appendChild(o);
                            })();
                        """)
                    except Exception:
                        pass
                try:
                    apply_update_and_relaunch(remote)
                    # Force immediate process termination. sys.exit(0) from a
                    # daemon thread only kills the thread - the main pywebview
                    # loop keeps running and holds the EXE handle, which
                    # causes swap.bat to time out waiting to rename. os._exit
                    # terminates the whole process with no unwind so the file
                    # lock is released immediately and the bat can swap.
                    os._exit(0)
                except Exception as e:
                    # Remove the overlay so the user can see/use the app again
                    if _window is not None:
                        try:
                            _window.evaluate_js("var o=document.getElementById('nbUpdateOverlay');o&&o.remove();")
                        except Exception:
                            pass
                    ctypes.windll.user32.MessageBoxW(
                        0, f"업데이트 실패:\n{e}\n\n현재 버전으로 계속 진행합니다.",
                        "NanoBanana", 0x10
                    )
        except Exception as e:
            print(f"  Update check: {e}")

    threading.Thread(target=_bg_update_check, daemon=True).start()

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
