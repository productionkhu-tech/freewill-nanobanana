# -*- coding: utf-8 -*-
"""Task 2: launcher / updater / app / mini_window take every per-edition name from edition.py
(spec 5.1). The company values must stay exactly what they were.
usage: python tests/personal/t_names_wired.py"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _h import REPO, check, done, run_py  # noqa: E402

PROBE = r"""
import edition as E, updater, launcher, mini_window, app as A
c = A.app.test_client()
idx = c.get("/").get_data(as_text=True)
mini = c.get("/mini").get_data(as_text=True)
os.environ.pop("NANOBANANA_DATA_DIR", None)
dd_default = A._user_data_dir()
import urllib.request as _ur
_seen = []
def _fake_urlopen(req, *a, **k):
    _seen.append(req.full_url if hasattr(req, "full_url") else str(req)); raise OSError("offline in test")
_ur.urlopen = _fake_urlopen
A._fetch_release_notes("v2026-01-0101")
notes_url_has_repo = bool(_seen) and ("/repos/%s/releases/" % E.REPO) in _seen[0]
print(json.dumps({
  "port": launcher.PORT, "E_port": E.PORT,
  "repo": updater.REPO, "E_repo": E.REPO,
  "assets": list(updater.asset_names()) if hasattr(updater, "asset_names") else None,
  "temps": [os.path.basename(x) for x in updater.temp_names(P["work"])] if hasattr(updater, "temp_names") else None,
  "stem": E.EXE_STEM,
  "state_dir": os.path.basename(os.path.dirname(updater.auto_update_state_file())),
  "dd_default": os.path.basename(dd_default), "E_dd": E.DATA_DIRNAME,
  "dd_under_home": os.path.dirname(dd_default) == P["home"],
  "dock": [mini_window.DOCK_NAME, mini_window.DOCK_RANK], "E_dock": [E.DOCK_NAME, E.DOCK_RANK],
  "index_title": ("<title>%s</title>" % E.APP_TITLE) in idx,
  "index_edition": ('<meta name="nb-edition" content="%s">' % E.EDITION) in idx,
  "mini_title": ("<title>%s</title>" % E.MINI_TITLE) in mini,
  "clip": os.path.basename(A.shared.temp_ref_dir), "E_clip": E.CLIPBOARD_DIRNAME,
  "proj": os.path.basename(A.shared.project_default_save_dir), "E_proj": E.PROJECTS_DIRNAME,
  "out": os.path.basename(A.state.output_dir), "E_out": E.OUTPUT_DIRNAME,
  "notes_repo": notes_url_has_repo,
}))
"""

for ed in ("company", "personal"):
    r = run_py(ed, PROBE)
    if "_error" in r:
        check(ed + " probe ran", False, r["_error"])
        continue
    check(ed + " launcher port", r["port"] == r["E_port"], r["port"])
    check(ed + " updater repo", r["repo"] == r["E_repo"], r["repo"])
    check(ed + " updater assets", r["assets"] == [r["stem"] + ".exe", r["stem"] + ".exe.sha256"], r["assets"])
    check(ed + " updater temp names", r["temps"] == [r["stem"] + ".new.exe", r["stem"] + ".exe.old"], r["temps"])
    check(ed + " updater state dir", r["state_dir"] == r["E_dd"], r["state_dir"])
    check(ed + " app data dir default", r["dd_default"] == r["E_dd"] and r["dd_under_home"], r["dd_default"])
    check(ed + " dock", r["dock"] == r["E_dock"], r["dock"])
    check(ed + " index title", r["index_title"])
    check(ed + " index edition meta", r["index_edition"])
    check(ed + " mini title", r["mini_title"])
    check(ed + " folders", (r["clip"], r["proj"], r["out"]) == (r["E_clip"], r["E_proj"], r["E_out"]))
    check(ed + " release notes repo", r["notes_repo"])
    if ed == "company":
        check("company temp names are the old ones", r["temps"] == ["NanoBanana.new.exe", "NanoBanana.exe.old"])

# No per-edition literal left outside edition.py (comments and docstrings aside).
LITERALS = ['"NanoBanana.new.exe"', '"NanoBanana.exe.old"', '".nanobanana"', "5656", '"NanoBanana JSON"',
            '"NanoBanana_Output"', '"NanoBanana Clipboard"', 'freewill-nanobanana', '"NanoBanana mini"',
            '"NanoBanana-AIImageStudio-SingleInstance-v1"', '"NanoBanana.AIImageStudio"', 'startswith("NanoBanana")']
left = []
for fn in ("app.py", "launcher.py", "updater.py", "mini_window.py"):
    src = open(os.path.join(REPO, fn), encoding="utf-8").read()
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    for i, line in enumerate(src.splitlines(), 1):
        code = line.split("#", 1)[0]
        for lit in LITERALS:
            if lit in code:
                left.append("%s: %s" % (fn, line.strip()[:90]))
check("no hardcoded per-edition literals", not left, "\n    " + "\n    ".join(left[:20]))
done()
