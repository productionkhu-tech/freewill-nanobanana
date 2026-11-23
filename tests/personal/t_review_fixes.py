# -*- coding: utf-8 -*-
"""Final review findings (2026-11-24), each reproduced before the fix:
1. the personal viewer's pid / cmp went after the token fragment (#nbt=...) - lost, so "compare" opened one
   picture and the viewer was no longer pinned to its tab;
2. opening a picture again in an open viewer did not reload it - the address differed only in the fragment;
3. pywebview deletes every cookie when a window starts - other windows' picture loads failed in that moment:
   the token cookie must come back by itself and a failed picture must be tried again;
4. the _MEI sweep trusted a folder rename to tell a running app's folder - a mapped DLL does not stop a rename.
usage: python tests/personal/t_review_fixes.py"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _h import REPO, check, done, isolate, run_py  # noqa: E402

isolate("personal")
import app as A  # noqa: E402
import launcher as L  # noqa: E402

# ---- 1 + 2: the viewer's address
made, loads = [], []
L.webview.create_window = lambda **kw: (made.append(kw["url"]), types.SimpleNamespace(events=types.SimpleNamespace(shown=[])))[1]
L.webview.windows = []
L._window = object()
A.shared.active_pid = "p7"
L.JsApi().open_viewer(r"C:\x\a.png", cmp=r"C:\x\b.png")
u = urllib.parse.urlsplit(made[0]) if made else urllib.parse.urlsplit("")
q = dict(urllib.parse.parse_qsl(u.query))
check("viewer: pid in the query, not the fragment", q.get("pid") == "p7", made[:1])
check("viewer: cmp in the query", q.get("cmp") == r"C:\x\b.png", q)
check("viewer: the fragment is only the token", u.fragment == "nbt=" + A.CSRF_TOKEN, u.fragment[:12])


class _Viewer:
    _nb_viewer = True
    native = types.SimpleNamespace(WindowState="Normal", Activate=lambda: None)

    def load_url(self, url):
        loads.append(url)

    def set_title(self, t):
        pass

    def show(self):
        pass


L.webview.windows = [_Viewer()]
L.JsApi().open_viewer(r"C:\x\a.png")
L.JsApi().open_viewer(r"C:\x\a.png")
check("viewer: the same picture again is a new address (reloads)",
      len(loads) == 2 and loads[0].split("#")[0] != loads[1].split("#")[0], loads)
check("viewer: still pinned and carrying the token", all("pid=p7" in x.split("#")[0] and x.endswith("#nbt=" + A.CSRF_TOKEN) for x in loads))

r = run_py("company", "import json, types, app as A, launcher as L\n"
           "made = []\n"
           "L.webview.create_window = lambda **kw: (made.append(kw['url']), types.SimpleNamespace(events=types.SimpleNamespace(shown=[])))[1]\n"
           "L.webview.windows = []; L._window = object(); A.shared.active_pid = 'p7'\n"
           "L.JsApi().open_viewer(r'C:\\x\\a.png', cmp=r'C:\\x\\b.png')\n"
           "print(json.dumps({'u': made[0]}))\n")
check("company viewer address unchanged",
      r.get("u") == "http://127.0.0.1:5656/viewer?path=C%3A%5Cx%5Ca.png&pid=p7&cmp=C%3A%5Cx%5Cb.png", r)

# ---- 3: the token cookie comes back after pywebview clears it; a failed picture is tried again
JS = r"""
const fs = require("fs"), vm = require("vm");
let cookie = "", timers = [], listeners = {};
const ctx = {
  location: {hash: "#nbt=TOK123", pathname: "/mini", search: ""},
  history: {replaceState() {}},
  document: {
    get cookie() { return cookie; }, set cookie(v) { cookie = v.split(";")[0]; },
    querySelector() { return null; },
    addEventListener(t, f) { (listeners[t] = listeners[t] || []).push(f); },
  },
  setInterval(f, ms) { timers.push(f); return timers.length; },
  setTimeout(f, ms) { timers.push(f); return timers.length; },
};
ctx.window = ctx;
ctx.addEventListener = (t, f) => { (listeners["w:" + t] = listeners["w:" + t] || []).push(f); };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
const out = {token: ctx.NB_TOKEN, first: cookie};
cookie = "";                                   // pywebview: DeleteAllCookies (a new window started)
timers.slice().forEach(f => f());
out.back = cookie;
const img = {tagName: "IMG", src: "http://127.0.0.1:35657/api/gallery/thumb?path=x&size=256", dataset: {}};
out.helper = typeof ctx.nbImgRetry === "function";
if (out.helper) {
  cookie = "";
  out.first_try = ctx.nbImgRetry(img);
  timers.slice().forEach(f => f());
  out.retried_src = img.src;
  out.cookie_after = cookie;
  out.second_try = ctx.nbImgRetry(img);
}
const ev = listeners["error"] || [];
out.capture = ev.length > 0;
console.log(JSON.stringify(out));
"""
jsf = os.path.join(tempfile.mkdtemp(), "probe.js")
open(jsf, "w", encoding="utf-8").write(JS)
r = subprocess.run(["node", jsf, os.path.join(REPO, "static", "nb_token.js")], capture_output=True, text=True)
import json  # noqa: E402
try:
    o = json.loads(r.stdout.strip().splitlines()[-1])
except Exception:
    o = {"_error": r.stderr[-400:]}
check("nb_token: token from the fragment", o.get("token") == "TOK123" and o.get("first") == "nbt=TOK123", o)
check("nb_token: the cookie comes back after it is cleared", o.get("back") == "nbt=TOK123", o)
check("nb_token: a failed picture is tried once more with the cookie back",
      o.get("first_try") is True and o.get("retried_src", "").startswith("http://127.0.0.1:35657/api/gallery/thumb?path=x&size=256&")
      and o.get("cookie_after") == "nbt=TOK123" and o.get("second_try") is False, o)
check("nb_token: pictures anywhere in the page get the retry", o.get("capture") is True, o)
mini = open(os.path.join(REPO, "templates", "mini.html"), encoding="utf-8").read()
check("mini thumbnails retry before giving up", "nbImgRetry(this" in mini and 'onerror="this.remove()"' not in mini)

# ---- 4: a folder whose python DLL another process has loaded is in use, even with no file open
T = tempfile.mkdtemp(prefix="nb_t_mapped_")
live = os.path.join(T, "_MEI51A2B")
os.makedirs(os.path.join(live, "templates"))
open(os.path.join(live, "templates", "index.html"), "w").write("x")
small = sorted((os.path.join(sys.base_prefix, "DLLs", n) for n in os.listdir(os.path.join(sys.base_prefix, "DLLs"))
                if n.endswith(".dll")), key=os.path.getsize)[0]
dll = os.path.join(live, "python399.dll")
shutil.copy2(small, dll)
CHILD = ("import ctypes, sys, time\nfrom ctypes import wintypes\nk = ctypes.WinDLL('kernel32')\n"
         "k.LoadLibraryW.restype = wintypes.HMODULE\nk.LoadLibraryW.argtypes = [wintypes.LPCWSTR]\n"
         "print('ok' if k.LoadLibraryW(sys.argv[1]) else 'fail', flush=True)\ntime.sleep(60)\n")
ch = subprocess.Popen([sys.executable, "-c", CHILD, dll], stdout=subprocess.PIPE, text=True)
loaded = ch.stdout.readline().strip()
dead = os.path.join(T, "_MEI6C7D")
os.makedirs(os.path.join(dead, "templates"))
open(os.path.join(dead, "python399.dll"), "w").write("x")
old = time.time() - 900
for p in (live, dead):
    os.utime(p, (old, old))
L._sweep_stale_mei_done = False
removed = L._sweep_stale_mei(temp_dir=T, our_mei=os.path.join(T, "_MEI0000"), force=True)
check("sweep: the DLL really is loaded by another process", loaded == "ok", loaded)
check("sweep: a folder whose python DLL is loaded stays whole",
      os.path.isfile(os.path.join(live, "templates", "index.html")) and os.path.isfile(dll), os.listdir(T))
check("sweep: a dead app's folder still goes", not os.path.exists(dead) and dead in removed, removed)
ch.kill()
ch.wait()
done()
