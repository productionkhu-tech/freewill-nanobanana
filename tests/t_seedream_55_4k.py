# -*- coding: utf-8 -*-
"""Seedream 5.5 4K (2026-11-27). The API now takes 5.5 sizes from 921,600 up to 8,294,400 px (= 3840x2160,
2880x2880); 2880x1620 and 3840x2160 used to be refused (11/10). There is no "4K" preset - "3K"/"4K" are
refused ("must be 'WIDTHxHEIGHT' or a supported size preset") - so 4K goes as exact pixels per ratio.
5.0 Pro / Flash stay at 4,624,220 (checked the same day). All checked without generating (a broken
reference makes BytePlus check the size first).
usage: python tests/t_seedream_55_4k.py   (no network, no money)"""
import base64
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "personal"))
from _h import check, done, isolate  # noqa: E402

P = isolate("company")
from PIL import Image  # noqa: E402

import app as A  # noqa: E402

A._init_keys = lambda: None
A.init_app()
A.shared.session_restoring = False
M55, MPRO, MFLASH = "seedream-5-5-261028", "seedream-5-0-pro-260628", "seedream-5-0-flash-260915"
MAXPX, MINPX = 8_294_400, 921_600
RATIOS = {"1:1": 1.0, "4:3": 4 / 3, "3:4": 3 / 4, "16:9": 16 / 9, "9:16": 9 / 16, "3:2": 1.5, "2:3": 2 / 3, "21:9": 21 / 9}


def cfg(model, aspect, res, refs=(), cw=0, ch=0):
    return A._gen_img_cfg(model, aspect, res, "", cw, ch, False, list(refs))


# 1. 5.5 4K: exact pixels for every ratio, inside the API's range, the ratio kept
check("5.5 lists 4K on top", list(A._SEEDREAM_SIZES[M55]) == ["1K", "1.5K", "2K", "4K"], list(A._SEEDREAM_SIZES[M55]))
c = cfg(M55, "16:9", "4K")
check("5.5 4K 16:9 = 3840x2160 (sent as pixels, no preset)", c.get("size") == "3840x2160" and "seedream_aspect" not in c, c)
for asp, r in RATIOS.items():
    s = cfg(M55, asp, "4K").get("size", "")
    m = re.fullmatch(r"(\d+)x(\d+)", s)
    w, h = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    check("5.5 4K %s -> %s inside 0.92M..8.29M px, ratio kept, 16-multiples" % (asp, s),
          m and MINPX <= w * h <= MAXPX and abs(w / h - r) / r < 0.01 and w % 16 == 0 and h % 16 == 0
          and w * h > 7_900_000)

# 2. what was there stays as it was
c = cfg(M55, "16:9", "2K")
check("5.5 2K unchanged (level + ratio in the prompt)", c.get("size") == "2K" and c.get("seedream_aspect") == "16:9", c)
c = cfg(MPRO, "16:9", "2K")
check("5.0 Pro 2K unchanged", c.get("size") == "2K" and c.get("seedream_aspect") == "16:9", c)
check("5.0 Pro / Flash: no 4K", "4K" not in A._SEEDREAM_SIZES[MPRO] and "4K" not in A._SEEDREAM_SIZES[MFLASH])

# 3. agents: 4K allowed for 5.5 only; an empty resolution means the top one (4K for 5.5)
check("agent: 5.5 4K accepted", A._agent_settings_problem(M55, "16:9", "4K", "") is None,
      A._agent_settings_problem(M55, "16:9", "4K", ""))
p = A._agent_settings_problem(MPRO, "16:9", "4K", "")
check("agent: 5.0 Pro 4K refused with the allowed list", p and p.get("error") == "bad_resolution"
      and p.get("allowed") == ["1K", "1.5K", "2K"], p)
check("agent default: 5.5 -> 4K, 5.0 Pro -> 2K",
      A._agent_default_resolution(M55) == "4K" and A._agent_default_resolution(MPRO) == "2K")

# 4. custom sizes: 5.5 up to 8.29M px, the others still 4.62M
w, h, _ = A._seedream_custom_size(M55, 3840, 2160)
check("5.5 custom 3840x2160 kept", (w, h) == (3840, 2160), (w, h))
w, h, n = A._seedream_custom_size(M55, 4096, 4096)
check("5.5 custom 4096x4096 shrunk to <= 8.29M", w * h <= MAXPX and "maxpx" in n, (w, h, n))
w, h, n = A._seedream_custom_size(MPRO, 3840, 2160)
check("5.0 Pro custom 3840x2160 still shrunk to 4.62M", w * h <= 4_624_220 and "maxpx" in n, (w, h))


# 5. 'auto' ratio at 4K: no preset to fall back on - the first reference's shape decides, else square
class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Fake:
    def __init__(self):
        self.sent = []
        b = io.BytesIO()
        Image.new("RGB", (64, 36)).save(b, format="PNG")
        self.b64 = base64.b64encode(b.getvalue()).decode("ascii")
        self.images = self

    def generate(self, **kw):
        self.sent.append(kw)
        return _Obj(data=[_Obj(b64_json=self.b64, background=None)], usage=None)


def run(aspect, refs):
    f = Fake()
    A.shared.client_seedream = f
    pils = [Image.open(io.BytesIO(b)) if b else None for b in refs]
    c = cfg(M55, aspect, "4K", pils)
    r = A.current_project()._generate_one_image_seedream({"index": 0, "total": 1, "seed": 1}, "test", refs, M55, c)
    return r, (f.sent[0] if f.sent else {})


def png(w, h):
    b = io.BytesIO()
    Image.new("RGB", (w, h), (50, 60, 70)).save(b, format="PNG")
    return b.getvalue()


r, kw = run("auto", [png(1600, 900)])
check("4K auto + a 16:9 reference -> 3840x2160", kw.get("size") == "3840x2160", kw.get("size"))
r, kw = run("auto", [None, png(900, 1600)])
check("4K auto + a 9:16 reference (empty first slot) -> 2160x3840", kw.get("size") == "2160x3840", kw.get("size"))
r, kw = run("auto", [])
check("4K auto, no reference -> square 2880x2880", kw.get("size") == "2880x2880", kw.get("size"))
# review 11/27: auto follows the reference's OWN shape (like GPT auto), not the nearest table ratio
for (rw, rh) in ((1000, 1250), (4000, 1000), (1234, 987)):
    r, kw = run("auto", [png(rw, rh)])
    m = re.fullmatch(r"(\d+)x(\d+)", kw.get("size", ""))
    w, h = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    check("4K auto + a %dx%d reference keeps its own shape, as big as allowed (%s)" % (rw, rh, kw.get("size")),
          m and abs((w / h) / (rw / rh) - 1) < 0.015 and 0.95 * MAXPX <= w * h <= MAXPX and w % 16 == 0 and h % 16 == 0)
r, kw = run("16:9", [png(900, 1600)])
check("4K 16:9 with a portrait reference stays 16:9 (asked ratio wins)", kw.get("size") == "3840x2160", kw.get("size"))
check("4K: no aspect sentence added to the prompt (the pixels say it)",
      "Output image aspect ratio" not in (kw.get("prompt") or ""), kw.get("prompt"))

# 6. the window's menus say the same
js = open(os.path.join(os.path.dirname(A.__file__), "static", "app.js"), encoding="utf-8").read()
spec = js[js.index('"seedream-5-5-261028": {'):]
spec = spec[:spec.index("},")]
check("app.js: 5.5 menu 1K/1.5K/2K/4K, starts at 4K",
      'resolutions: ["1K","1.5K","2K","4K"]' in spec and 'defaultResolution: "4K"' in spec, spec[:400])
check("app.js: 5.5 custom limit 8,294,400",
      re.search(r'"seedream-5-5-261028":\s*\[921600,\s*8294400\]', js) is not None)
done()
