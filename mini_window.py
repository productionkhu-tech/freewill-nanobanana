# -*- coding: utf-8 -*-
"""NanoBanana mini window (Windows) - v2026-11-1601.

A small always-on-top strip in a corner of the screen with only the work that is running, for when the main
window is put away and an agent (MCP) does the work (idea: 2026-11-13, designed with the user 11/13-11/16 in an
isolated demo; the rules: 기획_미니창_점검목록.md, ..\\Freewill_미니창_공통규칙.md). launcher.py creates it hidden
after the main window; the person shows it with the button next to the pin in the main window, and it shows by
itself when the main window is minimised and new work comes in.

What it reads comes from app.mini_summary() (app.py). Everything here is the window: its corner and size, its
record in the shared mini-dock folder so it stacks with Seedance's mini without overlapping, the native
right-click menu, and when to show and hide.

Print strings stay ASCII (CLAUDE.md rule 11)."""
import ctypes
import json
import os
import threading
import time
from ctypes import wintypes

import webview

W = 300                          # every size is 300 wide (user 11/16 "가로 길이 일정", Seedance's card is 300)
EDGE = 22                        # our card sits 22 in from the work area's edges (window 14 + Seedance's 8 of shadow)

A = None                         # the app module (app.py), set by start()
mini = None                      # the strip's pywebview window
OPEN_MAIN = {"fn": None}         # how a click on the strip brings the main window back (start() wires it)
# One of the two at a time (user 11/17 "미니창을 띄우면 당연히 본창은 없어져야", like Task Manager's compact view):
# the main window's button hides the main window (taskbar too) and shows the strip; the strip's open / row / x
# brings the main window back and puts the strip away. Never no window at all - NanoBanana has no tray icon.
MAIN = {"w": None, "hidden": False}

user32 = ctypes.WinDLL("user32", use_last_error=True)


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]


user32.MonitorFromWindow.restype = ctypes.c_void_p
user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
user32.MonitorFromPoint.restype = ctypes.c_void_p
user32.MonitorFromPoint.argtypes = [wintypes.POINT, ctypes.c_ulong]
user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_uint]
user32.GetDpiForWindow.argtypes = [ctypes.c_void_p]
user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
user32.SetLayeredWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_ubyte, ctypes.c_uint]
user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p
user32.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.IsIconic.argtypes = [ctypes.c_void_p]
user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
user32.GetForegroundWindow.restype = ctypes.c_void_p
_MONENUM = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(RECT), ctypes.c_ssize_t)
user32.EnumDisplayMonitors.argtypes = [ctypes.c_void_p, ctypes.c_void_p, _MONENUM, ctypes.c_ssize_t]
_shcore = ctypes.WinDLL("shcore")
_shcore.GetDpiForMonitor.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_uint),
                                     ctypes.POINTER(ctypes.c_uint)]


def _hwnd(w):
    h = w.native.Handle
    return h.ToInt64() if hasattr(h, "ToInt64") else int(h)


def _rect(h):
    r = RECT()
    user32.GetWindowRect(h, ctypes.byref(r))
    return r


def _work(h):
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    mon = user32.MonitorFromWindow(h, 2)
    user32.GetMonitorInfoW(mon, ctypes.byref(mi))
    return mon, mi.rcWork


def _per_monitor_process():
    """True when this process draws each monitor at its own scale (launcher sets it before pywebview starts).
    Then our window rects are the screen's own pixels, which the grow-and-wait below relies on."""
    try:
        return user32.GetAwarenessFromDpiAwarenessContext(user32.GetThreadDpiAwarenessContext()) == 2
    except Exception:
        return False


class _PerMonitor:
    """This thread per-monitor-v2 for one call: the screen's own pixels whatever the process mode is."""
    def __enter__(self):
        try:
            self.old = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            self.old = None
        return self

    def __exit__(self, *a):
        if self.old:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(self.old))


def _mon_scale(mon):
    dx, dy = ctypes.c_uint(), ctypes.c_uint()
    try:
        if _shcore.GetDpiForMonitor(ctypes.c_void_p(mon), 0, ctypes.byref(dx), ctypes.byref(dy)) == 0 and dx.value:
            return dx.value / 96.0
    except Exception:
        pass
    return 1.0


def _phys(h):
    """(window rect, its monitor's work area, that monitor's scale), in the screen's own pixels."""
    with _PerMonitor():
        r = _rect(h)
        mon, wa = _work(h)
        return (r.left, r.top, r.right, r.bottom), (wa.left, wa.top, wa.right, wa.bottom), _mon_scale(mon)


_MONS = {"at": 0.0, "v": []}


def _monitors(per_monitor=True):
    """[(work area, scale, primary)] - screen pixels, or (per_monitor=False) the rects this process sees."""
    if per_monitor and time.time() - _MONS["at"] < 2.0:
        return _MONS["v"]
    out = []

    def cb(hm, dc, rp, lp):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(hm, ctypes.byref(mi))
        w = mi.rcWork
        out.append(((w.left, w.top, w.right, w.bottom), _mon_scale(hm), bool(mi.dwFlags & 1)))
        return 1
    fn = _MONENUM(cb)
    if per_monitor:
        with _PerMonitor():
            user32.EnumDisplayMonitors(None, None, fn, 0)
        _MONS["at"], _MONS["v"] = time.time(), out
    else:
        user32.EnumDisplayMonitors(None, None, fn, 0)
    return out


def _corner_of(r, wa):
    cx, cy = (r.left + r.right) / 2, (r.top + r.bottom) / 2
    return ("t" if cy < (wa.top + wa.bottom) / 2 else "b") + ("l" if cx < (wa.left + wa.right) / 2 else "r")


# ---------------------------------------------------------------- prefs (mini_window.json, beside prefs.json)
PREFS = {"auto": True, "opacity": 1.0, "pinned": True, "corner": "tr", "screen": None, "size": "card"}


def _prefs_path():
    return os.path.join(A._user_data_dir(), "mini_window.json")


def _prefs_load():
    try:
        with open(_prefs_path(), "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            PREFS["auto"] = bool(d.get("auto", True))
            PREFS["opacity"] = min(1.0, max(0.3, float(d.get("opacity", 1.0))))
            PREFS["pinned"] = bool(d.get("pinned", True))
            if d.get("corner") in ("tr", "tl", "br", "bl"):
                PREFS["corner"] = d["corner"]
            if d.get("size") in ("pill", "card", "full"):
                PREFS["size"] = d["size"]
            s = d.get("screen")
            if isinstance(s, list) and len(s) == 2:
                PREFS["screen"] = [int(s[0]), int(s[1])]
    except Exception:
        pass


def _prefs_save():
    try:
        PREFS["corner"] = CORNER["v"]
        PREFS["pinned"] = LOCKED["v"]
        tmp = _prefs_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(PREFS, f)
        os.replace(tmp, _prefs_path())
    except Exception as e:
        print("mini prefs:", e, flush=True)


# ---------------------------------------------------------------- state
CORNER = {"v": "tr"}             # top-right by default: Windows toasts own the bottom-right (user 11/13)
LOCKED = {"v": True}             # the pin: on = a drop goes to the nearest corner and stacks, off = stays put
STATE = {"stacked": False}
MAIN_STATE = {"visible": True}   # the main window is up (not minimised)
MINI_STATE = {"visible": False}
AUTO = {"shown": False}          # shown by itself: hides by itself once the list is empty
DISMISS = {"v": False}           # closed by the person while work ran: no auto-show until that work is done
PREV = {"active": set(), "alerts": 0, "primed": False}
SIZE = {"v": "card"}             # told by the page with each fit()
DESIRED = {"h": None, "w": None}  # CSS px size the page asked for
DRAG = {"on": False}             # set by the page while its header is held
PLACE_LOCK = threading.Lock()

# ---------------------------------------------------------------- the mini-dock agreement (with Seedance)
# Each app writes only its own <dock>\<app>.json and reads the others. Same monitor, same corner: the one that
# came up first keeps the corner (since; within TIE_MS the rank decides: Seedance 0, its personal build 0.5,
# NanoBanana 1); the later one sits 22 past the REAL card of the one ahead (record x/y/w/h less `pad`, 8 when
# not written) - so it does not matter how either side counts its slot. Every size has one height, so our
# slot is our real height. Records are in "the screen's pixels / that monitor's scale" (Seedance since 11/16
# 18:47); we also write `px` (screen pixels) and read others relative to the work area, so a monitor whose
# origin differs between Electron and us still matches. 2 s heartbeat; a record older than 6 s is ignored.
DOCK_NAME, DOCK_RANK, DOCK_GAP, DOCK_MARGIN, DOCK_STALE_MS = "nanobanana", 1, 6, 14, 6000
DOCK_TOP = 14                    # window edges 14 from every side (user 11/16 "14 14 느낌"); cards land 22 in
CLAIM_MS, TIE_MS = 250, 300      # claim, wait, then look; closer than TIE_MS -> rank decides
SINCE = {"v": 0.0}               # ms this strip took its corner; 0 while hidden
CLAIM = {"on": False}            # between writing our since and showing: the record already says visible
VPAD = 8                         # our card + 8 all round is counted like a Seedance window (card + 8 of shadow)
STACK_GAP = DOCK_MARGIN + VPAD   # 22: card to card, the same as screen edge to card
DOCK = {"off": 0, "room": None, "entries": []}
DOCK_W = {"at": 0.0, "last": None}
LAST_GOOD = {}
GROW_WAIT = 1.3                  # s: the longest we hold a size change for an app behind us to make room
FUTURE = {"rect": None}          # physical rect we announced and are about to take
MIN_ROOM = 120                   # CSS px: less than this left above the app ahead -> sit beside it instead
PER_MONITOR_DPI = False


def _dock_dir():
    d = os.environ.get("NANOBANANA_MINI_DOCK_DIR", "").strip()
    if d:
        return d
    if os.environ.get("NANOBANANA_DATA_DIR", "").strip():
        # a test run never writes into the real folder, where Seedance would take it for a real strip (rule 17)
        return os.path.join(A._user_data_dir(), "mini-dock")
    return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Freewill", "mini-dock")


DOCK_DIR = None
CLOSING = {"v": False}           # the app is going away (close()): the strip may really close now


def _area_logical(wa, sc):
    return {"x": round(wa.left / sc), "y": round(wa.top / sc),
            "width": round((wa.right - wa.left) / sc), "height": round((wa.bottom - wa.top) / sc)}


def _same_area(e_area, a, k):
    try:
        return all(abs(float(e_area[n]) - a[n] * k) <= 1 for n in ("x", "y", "width", "height"))
    except Exception:
        return False


def _near(p, q, tol=1.5):
    return abs(float(p) - float(q)) <= tol


def _on_my_monitor(e, work, s):
    """Record e's window relative to the top-left of OUR work area (logical px), the frame its plain fields
    use (to write ours in) and its units (1 logical, s physical). None: another monitor, or can't tell.
    `px` (screen pixels) needs no guessing. Without it the plain fields are matched by size and scale, not by
    origin (Electron and we used to give a monitor that is not the primary different origins: y 286 vs 430)."""
    px = e.get("px")
    if isinstance(px, dict):
        try:
            pa = [float(v) for v in px["area"]]
            if not all(_near(pa[i], work[i], 2) for i in range(4)):
                return None
            return ((float(px["x"]) - work[0]) / s, (float(px["y"]) - work[1]) / s,
                    float(px["w"]) / s, float(px["h"]) / s), None, 1
        except (KeyError, TypeError, ValueError, IndexError):
            pass                                       # unreadable: try the plain fields
    try:
        area = e["area"]
        ax, ay, aw, ah = (float(area[n]) for n in ("x", "y", "width", "height"))
        ex, ey, ew, eh = (float(e[n]) for n in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    lw, lh = (work[2] - work[0]) / s, (work[3] - work[1]) / s
    k = 1 if _near(aw, lw) and _near(ah, lh) else (s if s != 1 and _near(aw, lw * s) and _near(ah, lh * s) else 0)
    if not k:
        return None
    try:
        es = float(e.get("scale") or 0)
    except (TypeError, ValueError):
        es = 0
    if es and abs(es - s) > 0.01:
        return None                                    # same size, another scale: another monitor
    twins = [m for m in _monitors() if _near((m[0][2] - m[0][0]) / m[1], lw) and _near((m[0][3] - m[0][1]) / m[1], lh)
             and abs(m[1] - s) < 0.01]
    if len(twins) > 1:                                 # monitors alike: the origin has to say which, on one axis
        def says(m):
            return _near(ax / k, m[0][0] / m[1]) or _near(ay / k, m[0][1] / m[1])
        if not says((work, s)) or any(says(m) for m in twins if tuple(m[0]) != tuple(work)):
            return None
    return ((ex - ax) / k, (ey - ay) / k, ew / k, eh / k), (area if k == 1 else None), k


def _records():
    """Fresh records of the other apps (6 s). A read caught while the other app swaps its file in counts as its
    last good read - "nobody there" sent us to the corner and back (seen 11/16 with the real Seedance)."""
    try:
        names = [f for f in os.listdir(DOCK_DIR) if f.endswith(".json") and f != DOCK_NAME + ".json"]
    except OSError:
        return []
    now, out = time.time() * 1000, []
    for f in names:
        try:
            with open(os.path.join(DOCK_DIR, f), encoding="utf-8") as fh:
                e = json.load(fh)
            LAST_GOOD[f] = e
        except Exception:
            e = LAST_GOOD.get(f)
        try:
            if isinstance(e, dict) and now - float(e.get("at") or 0) < DOCK_STALE_MS:
                out.append(e)
        except (TypeError, ValueError):
            continue
    return out


def _is_before(e):
    """Does record e sit nearer the corner than us (it came up first, or the rank says so)?"""
    try:
        rank = float(e.get("rank") or 0)
        es = float(e.get("since") or 0)
    except (TypeError, ValueError):
        return None
    rank_before = rank < DOCK_RANK or (rank == DOCK_RANK and str(e.get("app")) < DOCK_NAME)
    mine = SINCE["v"]
    return (es < mine) if (es > 0 and mine > 0 and abs(es - mine) > TIE_MS) else rank_before


def dock_offset(a, sc, corner, x0=None, x1=None, work=None, s=None):
    """How far the apps ahead of us in this corner push our card in from where it sits alone (logical px),
    and who they are: 22 past the nearest edge of their real cards over our columns x0..x1."""
    off, who = 0, []
    for e in _records():
        if not e.get("visible") or e.get("free") or e.get("corner") != corner:
            continue
        got = _on_my_monitor(e, work, s)
        if got is None:
            continue
        (rx, ry, rw, rh), _frame, k = got
        if not _is_before(e):
            continue
        try:
            pad = (VPAD if e.get("pad") is None else max(0.0, float(e.get("pad") or 0))) / k
        except (TypeError, ValueError):
            continue
        ex, ey, ew, eh = a["x"] + rx, a["y"] + ry, rw, rh      # into our own logical frame
        if ew <= 2 * pad or eh <= 2 * pad:
            continue
        if x0 is not None and (ex + ew - pad <= x0 or ex + pad >= x1):
            continue                                   # not over our columns
        top, bottom = ey + pad, ey + eh - pad          # its card
        if corner[0] == "t":
            push = bottom + STACK_GAP - (a["y"] + DOCK_TOP + VPAD)
        else:
            push = (a["y"] + a["height"] - DOCK_MARGIN - VPAD) - (top - STACK_GAP)
        off = max(off, push)
        who.append({"app": e.get("app"), "slot": e.get("slot"), "h": e.get("h"), "card": [round(top), round(bottom)],
                    "w": round(ew)})
    return max(0, round(off)), who


def _followers(a, corner, x0, x1, work, s):
    """The cards of the apps BEHIND us in this corner (they make room for us), in our logical frame."""
    out = []
    for e in _records():
        if not e.get("visible") or e.get("free") or e.get("corner") != corner:
            continue
        got = _on_my_monitor(e, work, s)
        if got is None:
            continue
        (rx, ry, rw, rh), _frame, k = got
        if _is_before(e) is not False:
            continue
        try:
            pad = (VPAD if e.get("pad") is None else max(0.0, float(e.get("pad") or 0))) / k
        except (TypeError, ValueError):
            continue
        ex = a["x"] + rx
        if x0 is not None and (ex + rw - pad <= x0 or ex + pad >= x1):
            continue
        out.append((e.get("app"), a["y"] + ry + pad, a["y"] + ry + rh - pad))
    return out


def layout(off, ww, wh, wa, corner, sc, side_w=0):
    """(x, y, height, mode), physical px. off = how far the apps ahead push us (logical px). Alone: 22 in.
    Taller than the room left: the window gets shorter and the page scrolls its list. Under MIN_ROOM left (a
    small screen with Seedance in 펼침): beside it instead. No room there either: on screen even over the
    other. side_w = the widest window nearer the corner, logical px. Pure arithmetic."""
    wl, wt, wr, wb = wa
    top = corner[0] == "t"
    room = round(((wb - wt) / sc - DOCK_TOP - DOCK_MARGIN - 2 * VPAD - off) * sc)    # card 22 from the far edge too
    if off and room < round(MIN_ROOM * sc) and side_w:
        h = max(round(28 * sc), min(wh, round(((wb - wt) / sc - DOCK_TOP - DOCK_MARGIN - 2 * VPAD) * sc)))
        side = round((DOCK_MARGIN + side_w + DOCK_GAP + VPAD) * sc)
        x = wr - side - ww if corner[1] == "r" else wl + side
        y = wt + round((DOCK_TOP + VPAD) * sc) if top else wb - round(EDGE * sc) - h
        if wl <= x and x + ww <= wr:
            return int(x), int(max(wt, min(y, wb - h))), int(h), "beside"
    h = max(round(28 * sc), min(wh, room))
    x = wr - round(EDGE * sc) - ww if corner[1] == "r" else wl + round(EDGE * sc)
    edge = round(((DOCK_TOP if top else DOCK_MARGIN) + VPAD + off) * sc)
    y = wt + edge if top else wb - edge - h
    y = max(wt, min(y, wb - h))
    mode = "squeezed" if h < wh else ("stacked" if off else "alone")
    return int(x), int(y), int(h), mode


def _dock_write(visible, force=False):
    """Our own record, for apps that sit near us. Rewritten when it changes, else every 2 s."""
    try:
        hwnd = _hwnd(mini)
        pr, pw, ps = _phys(hwnd)
        if FUTURE["rect"]:
            pr = FUTURE["rect"]                       # announced: where we are about to be (see _place_locked)
        frame = None                                  # plain fields in the frame of the record on this monitor
        for e in _records():
            got = _on_my_monitor(e, pw, ps)
            if got and got[1] is not None:
                frame = got[1]
                break
        if frame:
            ax, ay = float(frame["x"]), float(frame["y"])
            area = {n: frame[n] for n in ("x", "y", "width", "height")}
        else:
            ax, ay = pw[0] / ps, pw[1] / ps
            area = {"x": round(ax), "y": round(ay), "width": round((pw[2] - pw[0]) / ps),
                    "height": round((pw[3] - pw[1]) / ps)}
        hh = round((pr[3] - pr[1]) / ps) + 2 * VPAD
        vp = round(VPAD * ps)
        body = {"app": DOCK_NAME, "rank": DOCK_RANK, "corner": CORNER["v"],
                "x": round(ax + (pr[0] - pw[0]) / ps) - VPAD, "y": round(ay + (pr[1] - pw[1]) / ps) - VPAD,
                "w": round((pr[2] - pr[0]) / ps) + 2 * VPAD, "h": hh,
                "visible": bool(visible), "at": int(time.time() * 1000), "area": area,
                "free": not LOCKED["v"],     # a free strip is in no corner: nobody makes room for it
                "since": SINCE["v"] if visible else 0,
                "scale": ps, "slot": hh, "pad": VPAD,
                "px": {"area": list(pw), "x": pr[0] - vp, "y": pr[1] - vp, "w": pr[2] - pr[0] + 2 * vp,
                       "h": pr[3] - pr[1] + 2 * vp, "scale": ps}}
        key = json.dumps({k: v for k, v in body.items() if k != "at"}, sort_keys=True)
        if not force and key == DOCK_W["last"] and time.time() - DOCK_W["at"] < 2.0:
            return
        os.makedirs(DOCK_DIR, exist_ok=True)
        fp = os.path.join(DOCK_DIR, DOCK_NAME + ".json")
        tmp = fp + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(body, fh)
        os.replace(tmp, fp)                           # nobody reads a half-written file
        DOCK_W["at"], DOCK_W["last"] = time.time(), key
    except Exception as e:
        print("mini dock write:", e, flush=True)


def place():
    if not LOCKED["v"] or DRAG["on"]:          # free mode, or the strip itself is being dragged
        return
    with PLACE_LOCK:
        _place_locked()


def _place_locked():
    hwnd = _hwnd(mini)
    r = _rect(hwnd)
    mon, wa = _work(hwnd)
    sc = (user32.GetDpiForWindow(hwnd) or 96) / 96.0
    ww = round((DESIRED["w"] or W) * sc)
    wh = round(DESIRED["h"] * sc) if DESIRED["h"] else r.bottom - r.top
    # Windows will not make the window shorter than its minimum (per monitor scale): ask for that minimum,
    # or every round placed it at the asked height and then again at the real one - 5 px of jitter
    wh = max(wh, STATE.setdefault("min_h", {}).get(round(sc * 100), 0))
    corner = CORNER["v"]
    a = _area_logical(wa, sc)
    cw = DESIRED["w"] or W
    cx0 = a["x"] + a["width"] - EDGE - cw if corner[1] == "r" else a["x"] + EDGE
    _pr, pw, ps = _phys(hwnd)
    STATE["placed_mon"] = pw                       # where the strip sits: a drop elsewhere is a newcomer there
    off, who = dock_offset(a, sc, corner, cx0, cx0 + cw, pw, ps)
    side_w = max([e["w"] for e in who] or [0])
    DOCK["off"], DOCK["entries"] = off, who
    DOCK["room"] = round(a["height"] - DOCK_TOP - DOCK_MARGIN - 2 * VPAD - off)
    x, y, h, mode = layout(off, ww, wh, (wa.left, wa.top, wa.right, wa.bottom), corner, sc, side_w)
    if mode == "beside":
        DOCK["room"] = round(a["height"] - DOCK_TOP - DOCK_MARGIN - 2 * VPAD)
    if mode != STATE.get("mode"):
        STATE["mode"] = mode
        print("mini placement:", mode, "offset", off, flush=True)
    STATE["stacked"] = mode != "alone"
    if (x, y, ww, h) != (r.left, r.top, r.right - r.left, r.bottom - r.top):
        # Growing toward an app that sits behind us: tell it first and wait until it has moved, or we cover it
        # until it looks again (Seedance looks once a second). Shrinking or moving away needs no wait.
        top = corner[0] == "t"
        reach_old = r.bottom if top else r.top
        reach_new = y + h if top else y
        if PER_MONITOR_DPI and MINI_STATE.get("visible") and (reach_new > reach_old if top else reach_new < reach_old):
            def clash():
                edge = reach_new / sc
                return [f for f in _followers(a, corner, cx0, cx0 + cw, pw, ps)
                        if (f[1] < edge + STACK_GAP - 1 if top else f[2] > edge - STACK_GAP + 1)]
            if clash():
                FUTURE["rect"] = (x, y, x + ww, y + h)
                _dock_write(True, force=True)
                t_end = time.time() + GROW_WAIT
                while time.time() < t_end and clash():
                    time.sleep(0.05)
                    _dock_write(True)
                STATE["grow_waited"] = round(GROW_WAIT - max(0.0, t_end - time.time()), 2)
        user32.SetWindowPos(hwnd, None, x, y, ww, h, 0x0004 | 0x0010)   # NOZORDER | NOACTIVATE
        FUTURE["rect"] = None
        r2 = _rect(hwnd)
        real_h = r2.bottom - r2.top
        if real_h != h:
            if real_h > h:
                STATE.setdefault("min_h", {})[round(sc * 100)] = real_h
            x2, y2, _h, _m = layout(off, ww, real_h, (wa.left, wa.top, wa.right, wa.bottom), corner, sc, side_w)
            user32.SetWindowPos(hwnd, None, x2, y2, 0, 0, 0x0001 | 0x0004 | 0x0010)


def dock(corner=None):
    try:
        hwnd = _hwnd(mini)
        if corner is None:                      # after a drag: nearest corner where it was dropped
            mon, wa = _work(hwnd)
            CORNER["v"] = _corner_of(_rect(hwnd), wa)
        place()
    except Exception as e:
        print("mini dock:", e, flush=True)


def _free_resize():
    """Free strip changing size: keep the corner nearest the screen's edges where it is."""
    h = _hwnd(mini)
    r = _rect(h)
    mon, wa = _work(h)
    sc = (user32.GetDpiForWindow(h) or 96) / 96.0
    ww, wh = round(DESIRED["w"] * sc), round(DESIRED["h"] * sc)
    right = (r.left + r.right) / 2 > (wa.left + wa.right) / 2
    low = (r.top + r.bottom) / 2 > (wa.top + wa.bottom) / 2
    x = r.right - ww if right else r.left
    y = r.bottom - wh if low else r.top
    x = max(wa.left, min(x, wa.right - ww))
    y = max(wa.top, min(y, wa.bottom - wh))
    user32.SetWindowPos(h, None, int(x), int(y), ww, wh, 0x0004 | 0x0010)


# ---------------------------------------------------------------- showing and hiding
def _has_active(summ):
    return bool(summ["running"] or summ["queued"] or summ["alerts"]
                or any(not b["finished"] for b in summ["batches"]))


def _go_to_saved_screen():
    """Before the first show: the monitor the strip was on last time, if that monitor is still there."""
    s = PREFS.get("screen")
    if not s or STATE.get("screen_done"):
        return
    STATE["screen_done"] = True
    try:
        with _PerMonitor():
            mon = user32.MonitorFromPoint(wintypes.POINT(int(s[0]), int(s[1])), 0)    # MONITOR_DEFAULTTONULL
            if mon:
                user32.SetWindowPos(_hwnd(mini), None, int(s[0]) - 150, int(s[1]) - 100, 0, 0, 0x0001 | 0x0004 | 0x0010)
    except Exception as e:
        print("mini screen:", e, flush=True)


def _show_mini(auto):
    """Write our since first (the record says visible), wait CLAIM_MS so a strip coming up at the same moment
    sees us too, then take the place the order gives and only then appear (Seedance's show(), same order)."""
    if MINI_STATE["visible"]:
        if not auto:
            AUTO["shown"] = False
        return
    _go_to_saved_screen()
    if not SINCE["v"]:
        SINCE["v"] = time.time() * 1000
    CLAIM["on"] = True
    _dock_write(True, force=True)
    time.sleep(max(0.0, (SINCE["v"] + CLAIM_MS) / 1000 - time.time()))
    try:
        if LOCKED["v"]:
            with PLACE_LOCK:
                _place_locked()
    except Exception as e:
        print("mini place before show:", e, flush=True)
    fg = user32.GetForegroundWindow()
    mini.show()                              # pywebview's show() also activates the window...
    try:
        if fg and fg != _hwnd(mini):
            user32.SetForegroundWindow(fg)   # ...hand the keyboard back to whatever had it
    except Exception:
        pass
    MINI_STATE["visible"] = True
    AUTO["shown"] = bool(auto)
    CLAIM["on"] = False
    _dock_write(True, force=True)
    _remember_screen()


def _hide_mini():
    """Hidden: our corner is free again, and the record says so at once."""
    SINCE["v"] = 0
    CLAIM["on"] = False
    mini.hide()
    MINI_STATE["visible"] = False
    AUTO["shown"] = False
    _dock_write(False, force=True)


def _remember_screen():
    try:
        _pr, pw, _ps = _phys(_hwnd(mini))
        PREFS["screen"] = [(pw[0] + pw[2]) // 2, (pw[1] + pw[3]) // 2]
        _prefs_save()
    except Exception:
        pass


def _auto():
    """Shows by itself when the main window is minimised and something new comes in (a new batch, a new
    blocked request) - unless the person closed the strip while work was running, until that work is done.
    Shown by itself: closes by itself once the list is empty. Shown by the person: stays."""
    summ = A.mini_summary()
    # every batch, not every row: a new batch of the same job joins an existing x N row and is still new work
    active = {i for b in summ["batches"] if not b["finished"] for i in (b.get("ids") or [b["id"]])}
    n_alerts = sum(a["n"] for a in summ["alerts"])
    fresh = bool(active - PREV["active"]) or n_alerts > PREV["alerts"]
    PREV["active"], PREV["alerts"] = active, n_alerts
    busy = _has_active(summ)
    if not busy:
        DISMISS["v"] = False                 # what was running is done: the next new work may show it again
    if (PREV["primed"] and PREFS["auto"] and fresh and not DISMISS["v"] and not MAIN_STATE["visible"]
            and not MINI_STATE["visible"]):
        _show_mini(auto=True)
        print("mini auto: shown for new work", flush=True)
    PREV["primed"] = True
    if AUTO["shown"] and MINI_STATE["visible"] and not summ["batches"] and not busy:
        _hide_mini()
        print("mini auto: hidden, list empty", flush=True)


def main_minimized():
    """The person put the main window away: that is what the strip is for - lift the 'closed' block, and
    show it if something is running."""
    MAIN_STATE["visible"] = False
    DISMISS["v"] = False
    try:
        if PREFS["auto"] and not MINI_STATE["visible"] and _has_active(A.mini_summary()):
            threading.Thread(target=_show_mini, args=(True,), daemon=True).start()
    except Exception as e:
        print("mini main hidden:", e, flush=True)


def main_shown():
    """The main window is up again (restored from the taskbar, maximised): one of the two - the strip goes."""
    MAIN_STATE["visible"] = True
    MAIN["hidden"] = False
    if MINI_STATE["visible"] and not CLOSING["v"]:
        try:
            _hide_mini()
        except Exception as e:
            print("mini hide on main:", e, flush=True)


def set_main(w):
    """The main window (the test harness makes its own late)."""
    MAIN["w"] = w


def back_to_main(pid=None, billing=False):
    """The strip's open / row / notice / x: the main window first (on that tab), then the strip goes - in that
    order, so there is never a moment with no window."""
    fn = OPEN_MAIN["fn"]
    if fn:
        try:
            fn(pid, billing=bool(billing))
        except Exception as e:
            print("mini open_main:", e, flush=True)
    MAIN["hidden"] = False
    MAIN_STATE["visible"] = True
    if MINI_STATE["visible"]:
        _hide_mini()


def to_mini():
    """The main window's button: the strip first, then the main window goes (taskbar too)."""
    DISMISS["v"] = False
    _show_mini(auto=False)
    w = MAIN["w"]
    if w is not None and MINI_STATE["visible"]:
        try:
            w.hide()
            MAIN["hidden"] = True
            MAIN_STATE["visible"] = False
        except Exception as e:
            print("mini hide main:", e, flush=True)


def _apply_opacity(v):
    h = _hwnd(mini)
    ex = user32.GetWindowLongW(h, -20)
    if v >= 0.999:
        user32.SetWindowLongW(h, -20, ex & ~0x80000)            # not layered at all at 100%
    else:
        user32.SetWindowLongW(h, -20, ex | 0x80000)             # WS_EX_LAYERED
        user32.SetLayeredWindowAttributes(h, 0, int(round(255 * v)), 2)   # LWA_ALPHA


def _keep_on_top():
    """pywebview sets TopMost once, when it makes the form; the strip was found under a maximised Chrome with
    the flag gone (2026-11-13). Put it back whenever it is lost - one SetWindowPos, no activation."""
    h = _hwnd(mini)
    if not user32.GetWindowLongW(h, -20) & 0x8:                 # WS_EX_TOPMOST
        user32.SetWindowPos(h, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)   # HWND_TOPMOST


def _decorate():
    """Once, before it is first shown: rounded corners (Windows 11), no taskbar button / Alt+Tab entry (a
    tool window, like Seedance's skipTaskbar), the saved opacity."""
    try:
        h = _hwnd(mini)
        v = ctypes.c_int(2)                                     # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(h), 33, ctypes.byref(v), 4)
        ex = user32.GetWindowLongW(h, -20)
        user32.SetWindowLongW(h, -20, (ex | 0x80) & ~0x40000)   # WS_EX_TOOLWINDOW, not WS_EX_APPWINDOW
        _apply_opacity(PREFS["opacity"])
    except Exception as e:
        print("mini decorate:", e, flush=True)


def _watch():
    n = 0
    while not CLOSING["v"]:
        time.sleep(0.1)
        n += 1
        try:
            if mini is None:
                continue
            if MINI_STATE["visible"] or n % 10 == 0:
                _auto()                      # hidden: once a second is enough to notice new work
            if MINI_STATE["visible"]:
                place()
                _keep_on_top()
                _dock_write(True)
            elif CLAIM["on"]:
                _dock_write(True)            # hidden: _hide_mini wrote "not visible" once, nothing more to say
        except Exception as e:
            if not STATE.get("watch_err"):   # once, not ten times a second
                STATE["watch_err"] = True
                print("mini watch:", e, flush=True)


# ---------------------------------------------------------------- native right-click menu (same order as Seedance)
user32.CreatePopupMenu.restype = ctypes.c_void_p
user32.AppendMenuW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_wchar_p]
user32.TrackPopupMenu.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_void_p, ctypes.c_void_p]
user32.DestroyMenu.argtypes = [ctypes.c_void_p]
user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]

user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
MENU = [  # (id, action, label) - None is a separator
    (1, "main", "본창 열기"), None,
    (11, "size:pill", "크기: 알약"), (12, "size:card", "크기: 카드"), (13, "size:full", "크기: 펼침"), None,
    (21, "op:1", "투명도 100%"), (22, "op:0.85", "투명도 85%"), (23, "op:0.7", "투명도 70%"), (24, "op:0.55", "투명도 55%"), None,
    (31, "auto", "본창을 내렸을 때 새 작업이 생기면 띄우기"), (32, "close", "미니 창 닫기 (본창으로)"), None,
    (41, "quit", "앱 종료"),
]
MENU_DARK = {"done": False}


def _menu_follow_theme(hwnd):
    # Menus follow the Windows light/dark setting, as Electron's do (uxtheme ordinals; Windows 10 1903+).
    if MENU_DARK["done"]:
        return
    MENU_DARK["done"] = True
    try:
        ux = ctypes.WinDLL("uxtheme")
        ux[135](ctypes.c_int(1))                                   # SetPreferredAppMode(AllowDark)
        ux[133](ctypes.c_void_p(hwnd), ctypes.c_bool(True))      # AllowDarkModeForWindow
        ux[136]()                                                  # FlushMenuThemes
    except Exception as e:
        print("mini menu theme:", e, flush=True)


def _track_menu(size, opacity, auto, at=None):
    """Runs on the UI thread (the window's owner). Returns the picked action, or "" when dismissed."""
    hwnd = _hwnd(mini)
    _menu_follow_theme(hwnd)
    hm = user32.CreatePopupMenu()
    for it in MENU:
        if it is None:
            user32.AppendMenuW(hm, 0x800, 0, None)                  # MF_SEPARATOR
            continue
        mid, act, label = it
        on = ((act == "size:" + size) or (act.startswith("op:") and abs(float(act[3:]) - opacity) < 0.01)
              or (act == "auto" and auto))
        radio = act.startswith(("size:", "op:"))
        user32.AppendMenuW(hm, (0x200 if radio else 0) | (0x8 if on else 0), mid, label)   # MFT_RADIOCHECK, MF_CHECKED
    if at is None:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        x, y = pt.x, pt.y
    else:
        x, y = at
    user32.SetForegroundWindow(hwnd)                       # or the menu would not close on a click elsewhere
    cmd = user32.TrackPopupMenu(hm, 0x100 | 0x2 | 0x80, x, y, 0, hwnd, None)   # RETURNCMD | RIGHTBUTTON | NONOTIFY
    user32.PostMessageW(hwnd, 0, 0, 0)                     # WM_NULL, per the TrackPopupMenu notes
    user32.DestroyMenu(hm)
    return next((it[1] for it in MENU if it and it[0] == cmd), "")


def _menu_on_ui(size, opacity, auto, at=None):
    from System import Func, String
    box = {}

    def run():
        box["a"] = _track_menu(size, float(opacity or 1), bool(auto), at)
        return box["a"]
    mini.native.Invoke(Func[String](run))
    return box.get("a", "")


# ---------------------------------------------------------------- the page's bridge (window.pywebview.api)
class Api:
    def fit(self, h, w=None, size=None):
        try:
            if size in ("pill", "card", "full"):
                SIZE["v"] = size
                if PREFS.get("size") != size:     # kept for the next start (the page's own storage is not)
                    PREFS["size"] = size
                    _prefs_save()
            h = int(min(max(h, 28), 700))
            DESIRED["h"] = h
            DESIRED["w"] = int(min(max(w or W, 120), W))
            if LOCKED["v"]:
                place()                 # size and position together
            else:                       # free: grow away from the nearer screen edges, stay on screen
                _free_resize()
        except Exception as e:
            print("mini fit:", e, flush=True)

    def snap(self):
        DRAG["on"] = False
        if LOCKED["v"]:
            old, old_mon = CORNER["v"], STATE.get("placed_mon")
            CORNER["v"] = _corner_of(_rect(_hwnd(mini)), _work(_hwnd(mini))[1])
            try:
                new_mon = _phys(_hwnd(mini))[1]
            except Exception:
                new_mon = old_mon
            if CORNER["v"] != old or (old_mon is not None and new_mon != old_mon):
                SINCE["v"] = time.time() * 1000      # a newcomer in that corner - or on that monitor
            dock(None)                  # pinned: the drop goes to the nearest corner
        else:
            CORNER["v"] = _corner_of(_rect(_hwnd(mini)), _work(_hwnd(mini))[1])   # free: stays; remember its side
        _prefs_save()
        _remember_screen()

    def drag(self, on):
        DRAG["on"] = bool(on)
        if not on:
            place()

    def set_locked(self, on):
        LOCKED["v"] = PREFS["pinned"] = bool(on)
        if on:
            dock(None)                  # pinned again: go to the nearest corner
        _dock_write(MINI_STATE["visible"], force=True)
        _prefs_save()
        return LOCKED["v"]

    def open_main(self, pid=None, billing=False):
        if pid:
            A.mini_dismiss(pid_seen=pid)
        back_to_main(pid, billing)

    def dismiss(self, bid):
        A.mini_dismiss(bid=bid)

    def dismiss_finished(self):
        A.mini_dismiss(finished=True)

    def show_menu(self, size, opacity, auto):
        try:
            a = _menu_on_ui(str(size), opacity, auto)
        except Exception as e:
            print("mini menu:", e, flush=True)
            return "html"                       # the page falls back to its own menu
        if a.startswith("op:"):
            self.set_opacity(float(a[3:]))
        elif a == "auto":
            self.set_auto(not PREFS["auto"])
        elif a == "main":
            self.open_main()
        elif a == "close":
            self.close_mini()
        elif a == "quit":
            self.quit_app()
        return a

    def close_mini(self):
        # the strip's x: back to the main window (one of the two at a time; there is no tray to fall back to)
        back_to_main()

    def quit_app(self):
        """The strip's menu 'quit': the main window comes back and closes the usual way (it asks to save)."""
        back_to_main()
        w = MAIN["w"]
        if w is not None:
            def later():
                time.sleep(0.4)
                try:
                    user32.PostMessageW(_hwnd(w), 0x0010, 0, 0)      # WM_CLOSE -> the app's own close flow
                except Exception as e:
                    print("mini quit:", e, flush=True)
            threading.Thread(target=later, daemon=True).start()

    def set_opacity(self, v):
        PREFS["opacity"] = min(1.0, max(0.3, float(v)))
        _apply_opacity(PREFS["opacity"])
        _prefs_save()

    def set_auto(self, on):
        PREFS["auto"] = bool(on)
        _prefs_save()


def prefs_view():
    return {"auto": PREFS["auto"], "opacity": PREFS["opacity"], "pinned": LOCKED["v"], "size": PREFS.get("size", "card")}


def toggle():
    """The main window's button (and a second launch of the app): one of the two - strip on and the main window
    away, or the main window back and the strip away."""
    if MINI_STATE["visible"]:
        back_to_main()
    else:
        to_mini()
    return MINI_STATE["visible"]


def _focus_main(main_window, pid=None, billing=False):
    """A click on the strip: the main window comes back, on that tab when a row or a notice was clicked.
    The tab is switched BY THE PAGE (event mini_goto -> switchProject), never here: a switch on the server
    alone left the page showing tab A while its Generate went to tab B (review 2026-11-16)."""
    if pid:
        try:
            A.state.push_event({"pid": None, "type": "mini_goto", "target": pid, "billing": bool(billing)})
        except Exception as e:
            print("mini goto:", e, flush=True)
    try:
        h = _hwnd(main_window)
        if MAIN["hidden"]:
            main_window.show()                                   # hidden by the strip's button (not minimised)
            MAIN["hidden"] = False
        elif user32.IsIconic(h):
            user32.ShowWindow(h, 9)                              # SW_RESTORE
        user32.SetForegroundWindow(h)
        if user32.GetForegroundWindow() != h:
            # Windows refused the focus: at least bring it in front. A page that stays covered is throttled
            # by WebView2 and would not read the tab switch above until it is uncovered.
            user32.SetWindowPos(h, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)    # HWND_TOPMOST
            if not getattr(A.shared, "always_on_top", False):                                    # not the person's pin
                user32.SetWindowPos(h, ctypes.c_void_p(-2), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)  # HWND_NOTOPMOST
    except Exception as e:
        print("mini focus main:", e, flush=True)
    MAIN_STATE["visible"] = True


def start(app_module, url, main_window=None, open_main=None, api=None):
    """Create the strip, hidden, and wire it. Call before webview.start(). main_window: the app's window (its
    minimise/restore events drive the auto-show, a click on the strip brings it back). open_main: replaces
    that (the test harness). api: the page's bridge object (a subclass of Api in the test harness)."""
    global A, mini, DOCK_DIR, PER_MONITOR_DPI
    A = app_module
    DOCK_DIR = _dock_dir()
    PER_MONITOR_DPI = _per_monitor_process()
    _prefs_load()
    CORNER["v"] = PREFS["corner"]
    LOCKED["v"] = PREFS["pinned"]
    MAIN["w"] = main_window
    if open_main is not None:
        OPEN_MAIN["fn"] = open_main
    elif main_window is not None:
        OPEN_MAIN["fn"] = lambda pid=None, billing=False: _focus_main(main_window, pid, billing)
    if main_window is not None:
        try:
            main_window.events.minimized += (lambda *a: main_minimized())
            main_window.events.restored += (lambda *a: main_shown())
            main_window.events.maximized += (lambda *a: main_shown())
            main_window.events.shown += (lambda *a: main_shown())
        except Exception as e:
            print("mini main events:", e, flush=True)
    mini = webview.create_window("NanoBanana mini", url, js_api=api or Api(), width=W, height=200,
                                 resizable=False, frameless=True, min_size=(120, 28), easy_drag=False,
                                 on_top=True, hidden=True, background_color="#1C1C1E")

    def _loaded(*a):
        if STATE.get("watching"):
            return
        STATE["watching"] = True
        _decorate()
        threading.Thread(target=_watch, daemon=True).start()
    mini.events.loaded += _loaded
    mini.events.closing += _on_closing
    A.MINI_HOOKS["toggle"] = toggle
    A.MINI_HOOKS["visible"] = lambda: MINI_STATE["visible"]
    A.MINI_HOOKS["prefs"] = prefs_view
    return mini


def stop():
    """The app is going away: say so in the record at once (otherwise it goes stale after 6 s)."""
    try:
        if mini is not None:
            _dock_write(False, force=True)
    except Exception:
        pass


def close():
    """The main window closed: the strip goes too. pywebview ends the app only when every window is gone,
    so a hidden strip left behind would keep the process running with nothing on screen - holding port
    5656 and the single-instance mutex, so the app could not even be started again."""
    CLOSING["v"] = True
    stop()
    try:
        if mini is not None:
            mini.destroy()
    except Exception as e:
        print("mini close:", e, flush=True)
        os._exit(0)                          # the main window is already gone: nothing left to save


def _on_closing(*a):
    """Alt+F4 on the strip (it takes focus when clicked): hide it like its x, keep the window for next time.
    Only the app's own close() really closes it."""
    if CLOSING["v"]:
        return True
    threading.Thread(target=Api().close_mini, daemon=True).start()
    return False
