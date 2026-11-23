// Window token (v2026-11-23). Loaded first by every page (index, viewer, prompt popup, mini).
//
// The personal edition does not print the token into its pages - an outside program (an MCP connector)
// that reads the page gets nothing to send requests with. The launcher hands the token only to the
// windows it makes, in the address fragment (#nbt=...), which a browser never sends to the server. It is
// kept for the X-NB-Token header and in a session cookie, so <img> loads and other GETs carry it too, and
// then taken off the address. The company app has no fragment and keeps the token from <meta name="nb-csrf">.
//
// pywebview deletes every cookie whenever a window starts (private mode, one shared profile), so for a
// moment the other windows' picture loads have no cookie (review 2026-11-24). The personal pages therefore
// put the cookie back every second and on focus, and a picture that failed is tried once more.
(function () {
  var tok = "", viaCookie = false;
  try {
    var m = /(?:^#|&)nbt=([A-Za-z0-9_\-]+)/.exec(location.hash || "");
    if (m) {
      tok = m[1];
      viaCookie = true;
      history.replaceState(null, "", location.pathname + location.search);
    }
  } catch (e) { /* no fragment handling - fall through to the page's own token */ }
  if (!tok) {
    var meta = document.querySelector('meta[name="nb-csrf"]');
    tok = (meta && meta.content) || "";
  }
  if (!tok) {
    var c = /(?:^|;\s*)nbt=([A-Za-z0-9_\-]+)/.exec(document.cookie || "");
    if (c) { tok = c[1]; viaCookie = true; }
  }
  window.NB_TOKEN = tok;

  function putCookie() {
    try {
      if (viaCookie && tok && (document.cookie || "").indexOf("nbt=" + tok) === -1) {
        document.cookie = "nbt=" + tok + "; path=/; SameSite=Strict";
      }
    } catch (e) { /* ignore */ }
  }
  putCookie();
  if (viaCookie) {
    setInterval(putCookie, 1000);
    try { window.addEventListener("focus", putCookie); } catch (e) { /* ignore */ }
  }

  // A picture from this app that failed: put the cookie back and try once more a moment later.
  // true = it is being tried again (wait), false = the second try failed too (give up). Called both by the
  // listener below and by a picture's own onerror, so a pending retry answers true to both.
  window.nbImgRetry = function (img) {
    if (!viaCookie || !img || !img.src || !img.dataset || img.src.indexOf("/api/") === -1) return false;
    if (img.dataset.nbRetry === "1") return true;
    if (img.dataset.nbRetry) return false;
    img.dataset.nbRetry = "1";
    putCookie();
    setTimeout(function () {
      putCookie();
      img.dataset.nbRetry = "2";
      img.src = img.src + (img.src.indexOf("?") === -1 ? "?" : "&") + "_nbr=" + Date.now();
    }, 1200);
    return true;
  };
  // Pictures anywhere in the page (gallery cards, references): error events do not bubble, but they do
  // pass the document on their way down.
  try {
    document.addEventListener("error", function (e) {
      var t = e && e.target;
      if (t && t.tagName === "IMG") window.nbImgRetry(t);
    }, true);
  } catch (e) { /* ignore */ }
})();
