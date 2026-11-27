# -*- coding: utf-8 -*-
"""Seedream reference images stay inside BytePlus's input limits (2026-11-26 report from another PC:
400 InvalidParameter "the size of the input image (31.74 MiB) exceeds the limit (30 MiB)").

The app re-encodes every reference as a lossless PNG and sends it base64 in the request. A big photo-like
reference (4K and up) is 20+ MB as PNG and ~1/3 more as base64, so Seedream refused the whole request.
Limits (docs, "Image input limitations"): up to 30 MB per image, at most 6000x6000 = 36,000,000 px.
Not knowing whether BytePlus counts the file or its base64, the app keeps the base64 itself under the limit.
Small references must go exactly as before; transparent ones must stay transparent (PNG, rule 12).
usage: python tests/t_seedream_refs.py   (no network, no money: the Seedream client is a stand-in)"""
import base64
import io
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "personal"))
from _h import check, done, isolate  # noqa: E402

P = isolate("company")
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import app as A  # noqa: E402

A._init_keys = lambda: None
A.init_app()
A.shared.session_restoring = False
LIMIT = 30 * 1024 * 1024
MAX_PX = 6000 * 6000


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeSeedream:
    """Records what would be sent; answers with a tiny picture."""
    def __init__(self):
        self.sent = []
        buf = io.BytesIO()
        Image.new("RGB", (64, 36), (90, 90, 90)).save(buf, format="PNG")
        self._b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        self.images = self

    def generate(self, **kw):
        self.sent.append(kw)
        return _Obj(data=[_Obj(b64_json=self._b64, background=None)], usage=None)


def png(im):
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def noisy(w, h, mode="RGB"):
    rng = np.random.default_rng(7)
    base = np.linspace(0, 255, w, dtype=np.float32)[None, :, None] * np.ones((h, 1, 3), np.float32)
    rgb = np.clip(base + rng.normal(0, 40, (h, w, 3)), 0, 255).astype(np.uint8)
    if mode == "RGBA":
        a = np.full((h, w, 1), 255, np.uint8)
        a[:, : w // 3] = 0                                   # a real transparent third
        return Image.fromarray(np.concatenate([rgb, a], axis=2), "RGBA")
    return Image.fromarray(rgb, "RGB")


def send(refs):
    """Run the Seedream worker on these reference bytes; return the data URLs it sent."""
    fake = FakeSeedream()
    st = A.current_project()
    A.shared.client_seedream = fake
    r = st._generate_one_image_seedream({"index": 0, "total": 1, "seed": 1}, "[Image 1] test", refs,
                                        "seedream-5-5-261028", {"size": "2K", "output_format": "png"})
    img = fake.sent[0]["extra_body"]["image"] if fake.sent else None
    return r, ([img] if isinstance(img, str) else (img or []))


def decode(url):
    head, b64 = url.split(",", 1)
    return head, b64, Image.open(io.BytesIO(base64.b64decode(b64)))


# 1. a small reference goes exactly as before
small = png(Image.new("RGB", (512, 288), (200, 40, 40)))
r, urls = send([small])
check("small ref: generated", r.get("status") == "success", r.get("error"))
check("small ref: sent byte for byte as before",
      urls == ["data:image/png;base64," + base64.b64encode(small).decode("ascii")])

# 2. a big photo-like reference (PNG 23.6 MiB, base64 31.5 MiB - the reported failure)
big_im = noisy(3600, 2400)
big = png(big_im)
check("the big ref is over the limit as base64 (the reported case)", len(base64.b64encode(big)) > LIMIT,
      "%.2f MiB" % (len(base64.b64encode(big)) / 2**20))
t0 = time.time()
r, urls = send([big])
check("big ref: generated", r.get("status") == "success", r.get("error"))
head, b64, im = decode(urls[0])
check("big ref: sent under 30 MiB even as base64", len(b64) <= LIMIT, "%.2f MiB" % (len(b64) / 2**20))
check("big ref: a valid data URL (lowercase format)", head in ("data:image/png;base64", "data:image/jpeg;base64"), head)
check("big ref: same shape (aspect kept within 1%)", abs(im.width / im.height - 3600 / 2400) < 0.015, im.size)
check("big ref: not shrunk more than needed (long side >= 2048)", max(im.size) >= 2048, im.size)
print("  big ref took %.1fs, sent %s %dx%d %.2f MiB" % (time.time() - t0, head.split("/")[1].split(";")[0], im.width,
                                                         im.height, len(b64) / 2**20), flush=True)

# 3. a big transparent reference keeps its transparency
alpha_im = noisy(3600, 2400, "RGBA")
r, urls = send([png(alpha_im)])
head, b64, im = decode(urls[0])
check("transparent ref: still PNG", head == "data:image/png;base64", head)
check("transparent ref: under the limit", len(b64) <= LIMIT, "%.2f MiB" % (len(b64) / 2**20))
check("transparent ref: transparent pixels kept",
      im.mode in ("RGBA", "LA") and im.getchannel("A").getextrema()[0] == 0, im.mode)

# 4. too many pixels (small file, 7000x6000 = 42M px)
huge = png(Image.new("RGB", (7000, 6000), (30, 30, 30)))
r, urls = send([huge])
head, b64, im = decode(urls[0])
check("over 36M px: scaled to fit", im.width * im.height <= MAX_PX and abs(im.width / im.height - 7000 / 6000) < 0.01,
      im.size)

# 5. several refs: each one checked, slots in order, the small one untouched
r, urls = send([small, big, None])
check("several refs: two sent, in order", len(urls) == 2 and urls[0].endswith(base64.b64encode(small).decode("ascii")))
check("several refs: the big one fitted", len(urls[1].split(",", 1)[1]) <= LIMIT)

# 6. a batch of N pictures shares one fitting (the same bytes are not re-encoded per picture)
t0 = time.time()
send([big])
check("the same big ref again is fast (fitted once per batch)", time.time() - t0 < 2.0, "%.1fs" % (time.time() - t0))

# --- independent review 2026-11-26 ---
import threading  # noqa: E402

# 7. the pictures of one batch run at once: a new big ref is fitted once, not once per picture (memory: 10 x 45 MP
#    re-encodes at the same time took +4.3 GB)
big2 = png(noisy(3500, 2500))
runs0 = A._seedream_fit_runs
shared_fake = FakeSeedream()
A.shared.client_seedream = shared_fake
st = A.current_project()
out = []
ths = [threading.Thread(target=lambda i=i: out.append(st._generate_one_image_seedream(
    {"index": i, "total": 10, "seed": i}, "[Image 1] test", [big2], "seedream-5-5-261028",
    {"size": "2K", "output_format": "png"}))) for i in range(10)]
for t in ths:
    t.start()
for t in ths:
    t.join()
check("10 pictures at once: all generated", len(out) == 10 and all(r.get("status") == "success" for r in out))
check("10 pictures at once: the big ref fitted once", A._seedream_fit_runs - runs0 == 1, A._seedream_fit_runs - runs0)
check("10 pictures at once: all sent the same fitted ref",
      len(shared_fake.sent) == 10 and len({k["extra_body"]["image"] for k in shared_fake.sent}) == 1)

# 8. a ref that cannot be fitted fails that picture as 'nothing sent' (not 'may have been billed')
fake_before = A.shared.client_seedream
r, urls = send([b"this is not a picture"])
check("unreadable ref: fails, nothing sent", r.get("status") == "failed" and urls == [], (r.get("status"), len(urls)))
check("unreadable ref: not marked as possibly billed", not r.get("maybe_billed") and not r.get("usage"), r)
check("unreadable ref: says it is the reference", "ref" in (r.get("error") or "").lower(), r.get("error"))

# 9. the colour profile goes with the picture (an iPhone Display P3 / Adobe RGB photo must not change colour)
from PIL import ImageCms  # noqa: E402
icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
buf = io.BytesIO()
noisy(3600, 2400).save(buf, format="PNG", icc_profile=icc)
r, urls = send([buf.getvalue()])
head, b64, im = decode(urls[0])
check("colour profile kept when the ref is re-encoded", im.info.get("icc_profile") == icc, head)

# 10. only too many pixels (a line drawing / screenshot): stays PNG, no JPEG marks on lines and text
r, urls = send([huge])
head, b64, im = decode(urls[0])
check("over 36M px but small file: still PNG", head == "data:image/png;base64", head)

# 11. a full set of big refs (Seedream takes 10, 4.5 takes 14) is fitted once, then reused for every picture
many = []
base_arr = np.asarray(noisy(3500, 2500))
for i in range(10):
    a = base_arr.copy()
    a[i, :, :] = i
    many.append(png(Image.fromarray(a, "RGB")))
send(many)
runs1 = A._seedream_fit_runs
send(many)
check("10 big refs: the second picture reuses all of them", A._seedream_fit_runs == runs1, A._seedream_fit_runs - runs1)

# 12. the change is written down in the tab's log (what went, how big)
logs = " ".join(t for _, t in A.current_project().logs) if A.current_project().logs and isinstance(A.current_project().logs[0], tuple) \
    else " ".join(map(str, A.current_project().logs))
check("the log says a ref was fitted", "ref" in logs and "MiB" in logs and "->" in logs, logs[-300:])
done()
