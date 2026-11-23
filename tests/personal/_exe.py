# -*- coding: utf-8 -*-
"""Helpers for the EXE tests (Windows only): windows by process, UI Automation through uia.ps1, a guard that
the person's own apps are not running, and clean-up that only ever stops what the test itself started
(CLAUDE.md 4.5, memory 2026-11-17: never by image name)."""
import ctypes
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
u = ctypes.windll.user32
try:
    u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))      # coordinates in real screen pixels
except Exception:
    pass
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
u.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]


def port_open(port):
    s = socket.socket()
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def nb_processes():
    """[(pid, path)] of every NanoBanana*.exe running."""
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'NanoBanana*.exe' } | "
                        "ForEach-Object { '{0}|{1}' -f $_.ProcessId, $_.ExecutablePath }"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = []
    for line in r.stdout.splitlines():
        if "|" in line:
            pid, path = line.split("|", 1)
            out.append((int(pid), path))
    return out


def guard(ports=(5656, 35657)):
    for p in ports:
        if port_open(p):
            raise SystemExit("ABORT: port %d is in use - a NanoBanana is running. Ask the person to close it first." % p)
    if nb_processes():
        raise SystemExit("ABORT: a NanoBanana*.exe is running: %s. Ask the person to close it first." % nb_processes())


def stop_under(folder):
    """Stop the processes whose EXE lives in `folder` - and nothing else."""
    folder = os.path.normcase(os.path.abspath(folder))
    for pid, path in nb_processes():
        if path and os.path.normcase(os.path.abspath(path)).startswith(folder):
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)


def workdir(prefix):
    w = tempfile.mkdtemp(prefix=prefix)
    home = os.path.join(w, "home")
    for sub in ("Documents", "Desktop", "Pictures"):
        os.makedirs(os.path.join(home, sub), exist_ok=True)
    return w, home


def env_for(home, dock, extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("_MEI")}
    env.update({"USERPROFILE": home, "HOME": home, "NANOBANANA_MINI_DOCK_DIR": dock})
    env.pop("NANOBANANA_DATA_DIR", None)
    env.update(extra or {})
    return env


def get(port, path, token=None, timeout=10):
    """(status, body text)."""
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), headers={"X-NB-Token": token} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def post(port, path, body=None, token=None, timeout=20):
    h = {"Content-Type": "application/json"}
    if token:
        h["X-NB-Token"] = token
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), method="POST",
                                 data=json.dumps(body or {}).encode("utf-8"), headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def wait_version(port, secs=60):
    for _ in range(secs * 2):
        try:
            return json.loads(get(port, "/api/version", timeout=2)[1])["version"]
        except Exception:
            time.sleep(0.5)
    return None


def windows(pids=None):
    """{title: [hwnd]} of top-level windows (of these process ids when given)."""
    out = {}

    def cb(h, lp):
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pids is not None and pid.value not in pids:
            return True
        n = u.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(h, b, n + 1)
            out.setdefault(b.value, []).append(h)
        return True
    u.EnumWindows(EnumProc(cb), 0)
    return out


def pids_under(folder):
    folder = os.path.normcase(os.path.abspath(folder))
    return {pid for pid, path in nb_processes() if path and os.path.normcase(os.path.abspath(path)).startswith(folder)}


def visible(h):
    return bool(h) and bool(u.IsWindowVisible(h))


def rect(h):
    r = wintypes.RECT()
    u.GetWindowRect(h, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def uia(hwnd, mode="text", name=""):
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.path.join(HERE, "uia.ps1"),
                        "-Hwnd", str(int(hwnd)), "-Mode", mode, "-Name", name],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    return r.stdout


def wait_text(hwnd, needle, secs=20):
    t = ""
    for _ in range(secs):
        t = uia(hwnd)
        if needle in t:
            return True, t
        time.sleep(1)
    return False, t


def foreground_title():
    h = u.GetForegroundWindow()
    n = u.GetWindowTextLengthW(h)
    b = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(h, b, n + 1)
    return b.value


def dblclick(x, y):
    """A real double-click at screen pixel (x, y); the pointer goes back where it was."""
    old = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(old))
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.15)
    for _ in range(2):
        u.mouse_event(0x0002, 0, 0, 0, 0)
        u.mouse_event(0x0004, 0, 0, 0, 0)
        time.sleep(0.05)
    time.sleep(0.2)
    u.SetCursorPos(old.x, old.y)


def close_window(h):
    u.PostMessageW(h, 0x0010, 0, 0)


def copy_exe(src, work, name):
    dst = os.path.join(work, name)
    shutil.copy2(src, dst)
    return dst
