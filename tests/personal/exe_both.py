# -*- coding: utf-8 -*-
"""Both built EXEs at once (plan Task 10 step 4), in a throwaway home and temp folder: their own ports, windows
and data; the company page keeps its token while the personal one has none; the company app's start-up
_MEI sweep leaves the running personal app's files whole; each second launch goes back to its own app; and
the company, personal and a stand-in Seedance mini window share the top-right corner without overlapping
(Review Focus 5). Never touches the person's apps (guard + stop by folder).
usage: python tests/personal/exe_both.py [company exe] [personal exe]"""
import json
import os
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _h import check, done  # noqa: E402
import _exe as X  # noqa: E402

DIST = r"C:\NanoBanana_build\src\dist"
CO_SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DIST, "NanoBanana.exe")
PE_SRC = sys.argv[2] if len(sys.argv) > 2 else os.path.join(DIST, "NanoBanana-Personal.exe")
X.guard()
work, home = X.workdir("nb_exe_both_")
dock, tmp = os.path.join(work, "dock"), os.path.join(work, "tmp")
os.makedirs(tmp)
co_dir, pe_dir = os.path.join(work, "co"), os.path.join(work, "pe")
os.makedirs(co_dir)
os.makedirs(pe_dir)
co_exe = X.copy_exe(CO_SRC, co_dir, "NanoBanana.exe")
pe_exe = X.copy_exe(PE_SRC, pe_dir, "NanoBanana-Personal.exe")
import atexit  # noqa: E402
atexit.register(lambda: X.stop_under(work))
# own temp: the PyInstaller folders (and so the sweep) stay inside the test
base = {"TEMP": tmp, "TMP": tmp}
co_env = X.env_for(home, dock, dict(base, NANOBANANA_DATA_DIR=os.path.join(home, ".nanobanana")))   # rule 17
pe_env = X.env_for(home, dock, base)


def mei_dirs():
    return {d for d in os.listdir(tmp) if d.startswith("_MEI")}


# 1. personal first; its _MEI folder is then made to look an hour old
pe_proc = subprocess.Popen([pe_exe], cwd=pe_dir, env=pe_env)
check("personal up", X.wait_version(35657) is not None)
pe_mei = mei_dirs()
check("personal _MEI folder found", len(pe_mei) == 1, pe_mei)
old = time.time() - 3600
for d in pe_mei:
    for root, dirs, files in os.walk(os.path.join(tmp, d)):
        for n in dirs + files:
            try:
                os.utime(os.path.join(root, n), (old, old))
            except OSError:
                pass
    os.utime(os.path.join(tmp, d), (old, old))
# a dead app's leftover folder, also an hour old: the sweep must still clear those
dead = os.path.join(tmp, "_MEI999999")
os.makedirs(os.path.join(dead, "templates"))
open(os.path.join(dead, "templates", "x.html"), "w").write("x")
os.utime(dead, (old, old))

# 2. the company app starts and sweeps
co_proc = subprocess.Popen([co_exe], cwd=co_dir, env=co_env)
check("company up", X.wait_version(5656) is not None)
time.sleep(6)                                         # the sweep runs in the start-up update-check thread
check("dead leftover swept", not os.path.exists(dead) and not os.path.exists(dead + ".nbdel"))
for d in pe_mei:
    check("personal _MEI kept whole", os.path.isfile(os.path.join(tmp, d, "templates", "index.html"))
          and os.path.isfile(os.path.join(tmp, d, "static", "app.js")), d)
for path in ("/", "/mini", "/static/app.js", "/static/nb_token.js"):
    check("personal still serves " + path, X.get(35657, path)[0] == 200)

# 3. ports, windows, data, tokens
co_pids, pe_pids = X.pids_under(co_dir), X.pids_under(pe_dir)
cw, pw = X.windows(co_pids), X.windows(pe_pids)
co_main, pe_main = (cw.get("NanoBanana") or [None])[0], (pw.get("NanoBanana Personal") or [None])[0]
check("company window NanoBanana", X.visible(co_main), [t for t in cw if t.startswith("NanoBanana")])
check("personal window NanoBanana Personal", X.visible(pe_main), [t for t in pw if t.startswith("NanoBanana")])
check("data split", os.path.isdir(os.path.join(home, ".nanobanana")) and os.path.isdir(os.path.join(home, ".nanobanana-personal")))
co_html, pe_html = X.get(5656, "/")[1], X.get(35657, "/")[1]
tok = re.search(r'name="nb-csrf" content="([^"]+)"', co_html)
check("company page keeps its token", bool(tok))
check("personal page has none", not re.search(r'name="nb-csrf" content="[^"]+"', pe_html))
check("company GET open as before", X.get(5656, "/api/projects")[0] == 200)
check("personal GET closed", X.get(35657, "/api/projects")[0] == 403)
check("company token does not open the personal app", X.get(35657, "/api/projects", token=tok.group(1) if tok else "x")[0] == 403)

# 4. second launches go back to their own app
subprocess.run([pe_exe], cwd=pe_dir, env=pe_env, timeout=60)
time.sleep(2)
check("second personal launch -> personal window in front", X.foreground_title().startswith("NanoBanana Personal"), X.foreground_title())
subprocess.run([co_exe], cwd=co_dir, env=co_env, timeout=60)
time.sleep(2)
fg = X.foreground_title()
check("second company launch -> company window in front", fg.startswith("NanoBanana") and not fg.startswith("NanoBanana Personal"), fg)
check("still one of each", len(X.pids_under(co_dir)) == len(co_pids) and len(X.pids_under(pe_dir)) == len(pe_pids))

# 5. three mini windows in the top-right corner: a stand-in Seedance card first (rank 0, came up first)
import ctypes  # noqa: E402
from ctypes import wintypes  # noqa: E402
wa = wintypes.RECT()
ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(wa), 0)
scale = ctypes.windll.user32.GetDpiForSystem() / 96.0
sw, sh = int(316 * scale), int(180 * scale)
sx, sy = wa.right - int(14 * scale) - sw, wa.top + int(14 * scale)
since = int(time.time() * 1000) - 60000
stop = threading.Event()


def seedance():
    while not stop.is_set():
        rec = {"app": "seedance", "rank": 0, "corner": "tr", "visible": True, "free": False, "since": since,
               "at": int(time.time() * 1000), "scale": scale, "pad": 8,
               "area": {"x": round(wa.left / scale), "y": round(wa.top / scale), "width": round((wa.right - wa.left) / scale),
                        "height": round((wa.bottom - wa.top) / scale)},
               "x": round(sx / scale), "y": round(sy / scale), "w": round(sw / scale), "h": round(sh / scale),
               "px": {"area": [wa.left, wa.top, wa.right, wa.bottom], "x": sx, "y": sy, "w": sw, "h": sh, "scale": scale}}
        os.makedirs(dock, exist_ok=True)
        t = os.path.join(dock, "seedance.json.tmp")
        with open(t, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        os.replace(t, os.path.join(dock, "seedance.json"))
        stop.wait(1.0)


threading.Thread(target=seedance, daemon=True).start()
time.sleep(2)
r = X.post(5656, "/api/mini/toggle", {}, token=tok.group(1) if tok else None)
check("company mini on", '"visible": true' in r[1] or '"visible":true' in r[1], r)
time.sleep(1.5)
inv = X.uia(pe_main, "invoke", "미니창으로 보기")
check("personal mini on (its own button)", "INVOKED" in inv, inv)
time.sleep(4)
co_mini = (X.windows(co_pids).get("NanoBanana mini") or [None])[0]
pe_mini = (X.windows(pe_pids).get("NanoBanana Personal mini") or [None])[0]
check("both minis visible", X.visible(co_mini) and X.visible(pe_mini))
rects = {"seedance": (sx, sy, sx + sw, sy + sh)}
if co_mini and pe_mini:
    rects["company"], rects["personal"] = X.rect(co_mini), X.rect(pe_mini)


def overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


names = list(rects)
pairs = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]]
check("three minis do not overlap", len(rects) == 3 and not any(overlap(rects[a], rects[b]) for a, b in pairs), rects)
check("both stacked in the top-right corner", len(rects) == 3 and all(rects[k][2] > wa.right - 500 * scale and rects[k][1] < wa.top + 900 * scale
                                                                    for k in ("company", "personal")), rects)
recs = {n: json.load(open(os.path.join(dock, n), encoding="utf-8")) for n in os.listdir(dock) if n.endswith(".json")}
check("dock records: nanobanana 1, nanobanana-personal 1.5", (recs.get("nanobanana.json") or {}).get("rank") == 1
      and (recs.get("nanobanana-personal.json") or {}).get("rank") == 1.5, sorted(recs))
stop.set()

# 6. close both: each ends on its own
X.uia(pe_mini, "invoke", "본창 열기")
time.sleep(1)
X.post(5656, "/api/mini/toggle", {}, token=tok.group(1) if tok else None)
time.sleep(1)
X.close_window(pe_main)
X.close_window(co_main)
t0 = time.time()
while time.time() - t0 < 30 and (X.pids_under(co_dir) or X.pids_under(pe_dir)):
    time.sleep(0.5)
check("both end when their main windows close", not X.pids_under(co_dir) and not X.pids_under(pe_dir))
X.stop_under(work)
done()
