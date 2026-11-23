# -*- coding: utf-8 -*-
"""Task 3: the launcher's stale _MEI sweep must not touch a folder another running app is using
(spec 5.7). Before the fix it rmtree'd any _MEI older than 5 minutes with ignore_errors: the loaded
DLLs survived, but the templates / static files of a live app (company and personal running at once)
were deleted. usage: python tests/personal/t_mei_sweep.py"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _h import check, done, isolate  # noqa: E402

isolate("company")
import launcher as L  # noqa: E402

T = tempfile.mkdtemp(prefix="nb_t_temp_")


def mk(name, age, hold_open=None):
    p = os.path.join(T, name)
    for rel in ("templates/index.html", "static/app.js", "python3.dll"):
        fp = os.path.join(p, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w") as f:
            f.write("x")
    handle = open(os.path.join(p, hold_open), "r") if hold_open else None
    t = time.time() - age
    os.utime(p, (t, t))
    return p, handle


a, ha = mk("_MEI1A2B", 600, hold_open="python3.dll")      # a running app holds a file open
b, _ = mk("_MEI3C4D", 600)                                 # abandoned
c, _ = mk("_MEI5E6F", 60)                                  # just extracted
own, _ = mk("_MEI7788", 600)
left, _ = mk("_MEI99AA.nbdel", 600)                        # a sweep that died half-way
other = os.path.join(T, "_MEIZZ_not_ours")
os.makedirs(other)

gone = L._sweep_stale_mei(temp_dir=T, our_mei=own, now=time.time(), force=True)
check("in-use kept whole", all(os.path.isfile(os.path.join(a, r)) for r in ("templates/index.html", "static/app.js", "python3.dll")))
check("stale removed", not os.path.exists(b) and not os.path.exists(b + ".nbdel"))
check("fresh kept", os.path.isdir(c))
check("own kept", os.path.isdir(own))
check("half-done leftover removed", not os.path.exists(left))
check("non-matching name kept", os.path.isdir(other))
check("returned list", sorted(gone or []) == sorted([b, left]), gone)
ha.close()
check("no-arg call outside a frozen EXE does nothing", L._sweep_stale_mei() in (None, []))
done()
