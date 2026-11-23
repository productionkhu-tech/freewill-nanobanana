// Window token (v2026-11-23). Loaded first by every page (index, viewer, prompt popup, mini).
//
// The personal edition does not print the token into its pages - an outside program (an MCP connector)
// that reads the page gets nothing to send requests with. The launcher hands the token only to the
// windows it makes, in the address fragment (#nbt=...), which a browser never sends to the server. It is
// kept for the X-NB-Token header and in a session cookie, so <img> loads and other GETs carry it too, and
// then taken off the address. The company app has no fragment and keeps the token from <meta name="nb-csrf">.
(function () {
  var tok = "";
  try {
    var m = /(?:^#|&)nbt=([A-Za-z0-9_\-]+)/.exec(location.hash || "");
    if (m) {
      tok = m[1];
      document.cookie = "nbt=" + tok + "; path=/; SameSite=Strict";
      history.replaceState(null, "", location.pathname + location.search);
    }
  } catch (e) { /* no fragment handling - fall through to the page's own token */ }
  if (!tok) {
    var meta = document.querySelector('meta[name="nb-csrf"]');
    tok = (meta && meta.content) || "";
  }
  if (!tok) {
    var c = /(?:^|;\s*)nbt=([A-Za-z0-9_\-]+)/.exec(document.cookie || "");
    if (c) tok = c[1];
  }
  window.NB_TOKEN = tok;
})();
