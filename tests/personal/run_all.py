# -*- coding: utf-8 -*-
"""Every personal-edition test (source runs, no EXE, no money). usage: python tests/personal/run_all.py"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = ["t_edition.py", "t_names_wired.py", "t_mei_sweep.py", "t_personal_keys.py", "t_personal_app.py", "t_keys_status.py",
         "t_personal_paths.py", "t_token.py", "t_personal_races.py", "t_review_fixes.py"]
env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
bad = []
for t in TESTS:
    r = subprocess.run([sys.executable, os.path.join(HERE, t)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, timeout=600)
    last = [l for l in r.stdout.splitlines() if l.startswith("checks ")]
    print("%-22s %s" % (t, last[-1] if last else "NO RESULT (exit %d)" % r.returncode), flush=True)
    if r.returncode != 0:
        bad.append(t)
        print("\n".join(l for l in r.stdout.splitlines() if l.startswith("FAIL"))[:2000])
        print(r.stderr[-1500:])
print("\nALL GREEN" if not bad else "\nFAILED: " + ", ".join(bad))
sys.exit(1 if bad else 0)
