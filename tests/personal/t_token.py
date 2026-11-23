# -*- coding: utf-8 -*-
"""Task 7: the window token is not in the personal edition's HTML; only windows the launcher makes get it
(address fragment -> nb_token.js -> header + cookie), and every /api/* needs it there, GET included
(spec 5.5). The company app stays as it was. usage: python tests/personal/t_token.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _h import REPO, check, done, run_py  # noqa: E402

PROBE = r"""
import app as A
A._init_keys = lambda: None
c = A.app.test_client()
c2 = A.app.test_client()
T = A.CSRF_TOKEN
pages = [c.get(u).get_data(as_text=True) for u in ("/", "/viewer?path=x", "/prompt-popup?b64=&name=x", "/mini")]
out = {
  "html_token": any(T in h for h in pages),
  "script_first": all(h.find('src="/static/nb_token.js') != -1 and
                      h.find('src="/static/nb_token.js') < min([i for i in (h.find("<script", h.find('src="/static/nb_token.js') + 1), h.find("<link"), h.find("<style")) if i != -1] or [10**9])
                      for h in pages),
  "get_no_token": [c.get(u).status_code for u in ("/api/projects", "/api/status", "/api/gallery/thumb?path=x")],
  "post_no_token": c.post("/api/generate").status_code,
  "header": c.get("/api/status", headers={"X-NB-Token": T}).status_code,
  "wrong": c.get("/api/status", headers={"X-NB-Token": "nope"}).status_code,
  "version": c2.get("/api/version").status_code,
  "back": c2.post("/api/back-to-main").get_json(),
  "url_main": A.window_url("/"), "url_viewer": A.window_url("/viewer?path=a"),
  "T": T,
}
c.set_cookie("nbt", T)
out["cookie"] = c.get("/api/status").status_code
c3 = A.app.test_client(); c3.set_cookie("nbt", "nope")
out["cookie_wrong"] = c3.get("/api/status").status_code
print(json.dumps(out))
"""

pr = run_py("personal", PROBE)
check("personal probe ran", "_error" not in pr, pr.get("_error", ""))
check("personal: no token in any page", pr.get("html_token") is False)
check("personal: nb_token.js comes first", pr.get("script_first") is True)
check("personal: GET without token 403", pr.get("get_no_token") == [403, 403, 403], pr.get("get_no_token"))
check("personal: POST without token 403", pr.get("post_no_token") == 403)
check("personal: header ok", pr.get("header") == 200)
check("personal: wrong token 403", pr.get("wrong") == 403)
check("personal: cookie ok", pr.get("cookie") == 200)
check("personal: wrong cookie 403", pr.get("cookie_wrong") == 403)
check("personal: version open", pr.get("version") == 200)
check("personal: back-to-main open, harmless", pr.get("back") == {"ok": True, "shown": False}, pr.get("back"))
check("personal: window_url carries the token", pr.get("url_main") == "http://127.0.0.1:5657/#nbt=" + pr.get("T", "?")
      and pr.get("url_viewer") == "http://127.0.0.1:5657/viewer?path=a#nbt=" + pr.get("T", "?"), pr.get("url_main"))

cr = run_py("company", PROBE)
check("company probe ran", "_error" not in cr, cr.get("_error", ""))
check("company: token in the page as before", cr.get("html_token") is True)
check("company: GET open as before", cr.get("get_no_token", [0])[0] == 200 and cr.get("get_no_token", [0, 0])[1] == 200)
check("company: POST still needs the token", cr.get("post_no_token") == 403)
check("company: window_url plain", cr.get("url_main") == "http://127.0.0.1:5656/" and "#" not in (cr.get("url_viewer") or "#"))

js = open(os.path.join(REPO, "static", "nb_token.js"), encoding="utf-8").read() if os.path.isfile(os.path.join(REPO, "static", "nb_token.js")) else ""
check("nb_token.js: reads the fragment, sets the cookie, clears the address",
      "nbt=" in js and "document.cookie" in js and "SameSite=Strict" in js and "replaceState" in js)
appjs = open(os.path.join(REPO, "static", "app.js"), encoding="utf-8").read()
viewer = open(os.path.join(REPO, "templates", "viewer.html"), encoding="utf-8").read()
check("app.js and viewer use window.NB_TOKEN", "window.NB_TOKEN" in appjs and "window.NB_TOKEN" in viewer)
launcher = open(os.path.join(REPO, "launcher.py"), encoding="utf-8").read()
check("launcher builds every window address with window_url", launcher.count("window_url(") >= 4, launcher.count("window_url("))
done()
