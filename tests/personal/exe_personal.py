# -*- coding: utf-8 -*-
"""The built personal EXE on its own (plan Task 10 step 5), in a throwaway home: it is the personal edition
even when the environment says company, its page carries no window token and outside programs get 403,
yet its own windows (main, viewer, mini) get the token and work; a second launch brings the main window
back; closing the main window ends the app. Never touches the person's apps (guard + stop by folder).
usage: python tests/personal/exe_personal.py [path to NanoBanana-Personal.exe] [expected version]"""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _h import REPO, check, done  # noqa: E402
import _exe as X  # noqa: E402

EXE = sys.argv[1] if len(sys.argv) > 1 else r"C:\NanoBanana_build\src\dist\NanoBanana-Personal.exe"
VER = sys.argv[2] if len(sys.argv) > 2 else open(os.path.join(REPO, "VERSION")).read().strip()
PORT = 35657
X.guard()
work, home = X.workdir("nb_exe_personal_")
dock = os.path.join(work, "dock")
exe = X.copy_exe(EXE, work, "NanoBanana-Personal.exe")
import atexit  # noqa: E402
atexit.register(lambda: X.stop_under(work))

# a tab with one picture, so the viewer can be opened (made by the source app in the same throwaway home)
SEED = r'''
import os, sys, json
sys.path.insert(0, %r)
from PIL import Image
import app as A
A._init_keys = lambda: None
A.shared.session_restoring = False
out = os.path.join(os.path.expanduser("~"), "Desktop", A._ed.OUTPUT_DIRNAME) if hasattr(A._ed, "OUTPUT_DIRNAME") else os.path.expanduser("~/Desktop")
os.makedirs(out, exist_ok=True)
png = os.path.join(out, "seed_0001.png")
Image.new("RGB", (256, 256), (230, 190, 60)).save(png)
p = A.create_project(activate=True)
p.add_gallery_item(png, "seed prompt for the viewer", 1.0, "test", "1:1", "1K")
fp = os.path.join(os.path.expanduser("~"), "Documents", A._ed.PROJECTS_DIRNAME, "seed.json")
p.save_project(fp)
A._save_session()
print(json.dumps({"fp": fp, "session": open(A._session_file(), encoding="utf-8").read()}))
''' % REPO
r = subprocess.run([sys.executable, "-c", SEED], capture_output=True, text=True, encoding="utf-8", errors="replace",
                   env=X.env_for(home, dock, {"NANOBANANA_EDITION": "personal", "PYTHONIOENCODING": "utf-8"}), cwd=REPO)
try:
    seed = json.loads(r.stdout.strip().splitlines()[-1])
except Exception:
    seed = {}
check("seed tab made", seed.get("fp") and "seed.json" in seed.get("session", ""), (r.stderr or r.stdout)[-600:])

# the environment says company: a frozen EXE must not care (only its bundled EDITION decides)
env = X.env_for(home, dock, {"NANOBANANA_EDITION": "company"})
proc = subprocess.Popen([exe], cwd=work, env=env)
v = X.wait_version(PORT)
check("starts on 35657 with the version", v == VER, v)
check("does not take the company port", not X.port_open(5656))
time.sleep(4)
st, html = X.get(PORT, "/")
m = re.search(r'name="nb-csrf" content="([^"]*)"', html)
check("page has no window token", st == 200 and (m is None or m.group(1) == ""), m and m.group(1)[:6])
check("page says personal", 'name="nb-edition" content="personal"' in html and "<title>NanoBanana Personal</title>" in html)
check("outside GET refused", X.get(PORT, "/api/projects")[0] == 403)
check("outside POST refused", X.post(PORT, "/api/generate", {"prompt": "x"})[0] == 403)
check("outside mini toggle refused", X.post(PORT, "/api/mini/toggle")[0] == 403)
check("data in ~/.nanobanana-personal only", os.path.isdir(os.path.join(home, ".nanobanana-personal"))
      and not os.path.exists(os.path.join(home, ".nanobanana")), os.listdir(home))

pids = X.pids_under(work)
main, w = None, {}
for _ in range(60):                                  # a busy PC shows the main window late
    w = X.windows(pids)
    main = next((h for h in (w.get("NanoBanana Personal") or []) if X.visible(h)), None)
    if main:
        break
    time.sleep(0.5)
mini = (w.get("NanoBanana Personal mini") or [None])[0]
check("main window titled NanoBanana Personal", X.visible(main), list(w))
for _ in range(20):                                  # the mini window is made after the main one shows
    if mini:
        break
    time.sleep(0.5)
    mini = (X.windows(pids).get("NanoBanana Personal mini") or [None])[0]
check("mini window made, hidden", bool(mini) and not X.visible(mini))

# the main window got the token: its key button and banner come from token-only routes
ok, text = X.wait_text(main, "내 키")
check("main window: my-keys button (token reached the page)", ok and "0/3" in text, text[:300])
check("main window: personal banner", "개인판" in text)
open(os.path.join(work, "main_text.txt"), "w", encoding="utf-8").write(text)
check("main window: no team/project prompt", "팀·프로젝트" not in text and "팀/프로젝트" not in text,
      [l for l in text.splitlines() if "팀" in l][:5])
check("main window: the seeded tab is there", "seed_0001" in text or "seed prompt" in text, "")

# viewer: double-click the picture (a real click, the pointer goes back). A locked screen takes no clicks:
# then this part is reported as skipped, not passed.
if X.screen_locked():
    print("SKIP viewer: the screen is locked - real clicks reach no app", flush=True)
else:
    X.bring_front(main)
    card = X.uia(main, "rect", "seed_0001").split()     # the card's own text (not its buttons)
    before = set(X.windows(pids))
    clicked = False
    if len(card) == 4:
        x, y, cw, ch = map(int, card)
        clicked = X.dblclick(x + cw // 2, y + ch // 2, pids=pids)   # only onto the test's own window
    check("viewer: the card was on top to click", clicked, card)
    new = []
    for _ in range(30):                                  # the viewer window can take a few seconds on a busy PC
        time.sleep(0.5)
        new = [t for t in X.windows(pids) if t not in before]
        if new:
            break
    X.bring_front(main, on=False)
    viewer = (X.windows(pids).get(new[0]) or [None])[0] if new else None
    check("viewer window opened", bool(viewer), (card, new))
    if viewer:
        ok, vt = X.wait_text(viewer, "seed_0001", secs=15)
        check("viewer loaded with the token (shows the picture's name)", ok, vt[:300])
        X.close_window(viewer)
        time.sleep(1)


# mini: the main window's button (token-only route) -> the mini window shows, the main window hides
inv = X.uia(main, "invoke", "미니창으로 보기")
time.sleep(2)
check("mini button pressed", "INVOKED" in inv, inv)
check("mini shows, main hides", X.visible(mini) and not X.visible(main))
ok, mt = X.wait_text(mini, "오늘", secs=15)
check("mini window loaded with the token (today's count)", ok, mt[:300])
dockf = os.path.join(dock, "nanobanana-personal.json")
rec = json.load(open(dockf, encoding="utf-8")) if os.path.isfile(dockf) else {}
check("dock record nanobanana-personal rank 1.5", rec.get("app") == "nanobanana-personal" and rec.get("rank") == 1.5, rec.get("app"))

# a second launch: the running app brings its main window back and the new one exits
r2 = subprocess.run([exe], cwd=work, env=env, timeout=60)
time.sleep(2)
check("second launch exits quietly", r2.returncode == 0, r2.returncode)
check("second launch: main window back, mini away", X.visible(main) and not X.visible(mini))

# close: the whole app ends (the hidden mini window must go with it)
X.close_window(main)
t0 = time.time()
while time.time() - t0 < 25 and X.pids_under(work):
    time.sleep(0.5)
check("closing the main window ends the app", not X.pids_under(work), X.pids_under(work))
check("port 35657 free again", not X.port_open(PORT))
X.stop_under(work)
done()
