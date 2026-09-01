/**
 * NanoBanana key gateway — Cloudflare Worker edition.
 *
 * Same idea as the Python gateway, with nothing to keep switched on: the
 * provider key lives in Cloudflare's encrypted secret store, the app carries a
 * per-person token, and the address never changes because there is no machine
 * behind it.
 *
 * Enrollment: the app presents a TICKET everyone already has (the retired
 * OpenAI key still in their environment) and gets a token of its own. Tickets
 * are compared by SHA-256, so the ticket value is never stored here.
 *
 * Bodies are streamed straight through in both directions. Buffering a 15MB
 * generation would burn the CPU budget for no reason, and image edits are
 * multipart — re-encoding either is just a second place for bugs to live.
 *
 * Bindings this Worker needs:
 *   KV namespace : NB_TOKENS
 *   Secret       : OPENAI_KEY        the real provider key
 *   Secret       : TICKET_HASHES     comma-separated SHA-256 of accepted tickets
 *   Secret       : ADMIN_KEY         for the admin routes
 *   Variable     : ENROLL_OPEN       "1" while enrolling, "0" once everyone is in
 */

const ALLOWED = new Set([
  "/v1/images/generations",
  "/v1/images/edits",
  "/v1/models",
]);

const UPSTREAM = "https://api.openai.com";

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });

async function sha256Hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Comparison that does not finish early on the first wrong character. */
function timingSafeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** A paste often drags a BOM or a newline along; those hash to something else. */
const clean = (s) => (s || "").replace(/﻿/g, "").trim();

function bearer(request) {
  const h = request.headers.get("Authorization") || "";
  return h.toLowerCase().startsWith("bearer ") ? h.slice(7).trim() : "";
}

function clientIp(request) {
  return request.headers.get("CF-Connecting-IP") || "?";
}

async function adminOk(request, env) {
  const got = request.headers.get("X-Admin-Key") || "";
  return Boolean(env.ADMIN_KEY) && timingSafeEqual(got, env.ADMIN_KEY);
}

async function ticketOk(ticket, env) {
  const t = clean(ticket);
  if (!t) return false;
  const got = await sha256Hex(t);
  const want = (env.TICKET_HASHES || "").split(",").map((h) => h.trim().toLowerCase()).filter(Boolean);
  for (const h of want) if (timingSafeEqual(got, h)) return true;
  return false;
}

// ---------------------------------------------------------------- enrollment
async function handleEnroll(request, env) {
  if ((env.ENROLL_OPEN || "1") !== "1") {
    return json({ ok: false, error: "enrollment is closed" }, 423);
  }
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ ok: false, error: "bad request" }, 400);
  }
  const user = String(body.user || "unknown").slice(0, 64);
  const machine = String(body.machine || "unknown").slice(0, 64);
  const version = String(body.app_version || "").slice(0, 32);
  const ip = clientIp(request);

  // Slow down anyone hammering the one route reachable without a credential.
  const failKey = `fail:${ip}`;
  const fails = parseInt((await env.NB_TOKENS.get(failKey)) || "0", 10);
  if (fails >= 10) {
    return json({ ok: false, error: "too many attempts, try later" }, 429);
  }

  if (!(await ticketOk(body.ticket, env))) {
    await env.NB_TOKENS.put(failKey, String(fails + 1), { expirationTtl: 600 });
    return json({ ok: false, error: "ticket not accepted" }, 403);
  }

  // Reinstalling the app must not mint an endless list of tokens per person.
  const ident = (await sha256Hex(`${user}|${machine}`)).slice(0, 16);
  const existing = await env.NB_TOKENS.get(`ident:${ident}`);
  if (existing) {
    const rec = JSON.parse(existing);
    if (!rec.revoked) {
      return json({ ok: true, token: rec.token, token_id: rec.token_id, reused: true });
    }
  }

  const raw = crypto.getRandomValues(new Uint8Array(24));
  const token = "nbt_" + [...raw].map((b) => b.toString(16).padStart(2, "0")).join("");
  const tokenId = (await sha256Hex(token)).slice(0, 16);
  const rec = {
    token, token_id: tokenId, ident, user, machine,
    app_version: version, issued_at: new Date().toISOString().slice(0, 19),
    calls: 0, revoked: false,
  };
  await env.NB_TOKENS.put(`tok:${tokenId}`, JSON.stringify(rec));
  await env.NB_TOKENS.put(`ident:${ident}`, JSON.stringify(rec));
  return json({ ok: true, token, token_id: tokenId, reused: false });
}

// ----------------------------------------------------------------- key issue
/**
 * Hand back the current provider key to a machine that proves it already has
 * one we recognise.
 *
 * The trade this makes: the key ends up on every machine, so it can be copied.
 * What it buys is that rotating it stops being a visit to 70 desks — change it
 * here and every app picks the new one up the next time it starts. That turns
 * "someone left the company" from a week of work into a one-line edit, which is
 * the thing that actually went wrong.
 *
 * TICKET_HASHES holds the hash of every key that may ask: the retired one to
 * begin with, plus each key that has since been issued, so a machine can always
 * present whatever it currently holds.
 */
async function handleKey(request, env, ctx) {
  // A personal token, not the raw ticket.
  //
  // Handing the key to whoever presents the retired key would promote that key
  // from harmless to as valuable as the real one — and it has been sitting on
  // ~70 machines for months, including ones belonging to people who have since
  // left. A token is issued once, during enrollment, and can be revoked for one
  // machine without touching anyone else. Once enrollment is closed, a leaked
  // ticket is worth nothing.
  const token = bearer(request);
  if (!token) {
    return json({ ok: false, error: "token required" }, 401);
  }
  const tokenId = (await sha256Hex(token)).slice(0, 16);
  const raw = await env.NB_TOKENS.get(`tok:${tokenId}`);
  if (!raw) {
    return json({ ok: false, error: "invalid token" }, 401);
  }
  const rec = JSON.parse(raw);
  if (rec.revoked || !timingSafeEqual(rec.token, token)) {
    return json({ ok: false, error: "revoked token" }, 401);
  }
  if (!env.OPENAI_KEY) {
    return json({ ok: false, error: "no key configured" }, 503);
  }

  // The client sends its app_version alongside the fetch; recording it turns
  // the admin list into a live "which build is each machine on" roster —
  // exactly what a staged rollout needs to see who is lagging.
  let ver = "";
  try {
    const b = await request.json();
    ver = String(b.app_version || "").slice(0, 32);
  } catch {}

  // Who collected it and when — the only trail there is once a key is out.
  // Through waitUntil: a promise left running loose is killed the moment the
  // response goes out, and the record silently never lands.
  ctx.waitUntil((async () => {
    try {
      const cur = await env.NB_TOKENS.get(`tok:${tokenId}`);
      if (!cur) return;
      const r = JSON.parse(cur);
      r.key_fetches = (r.key_fetches || 0) + 1;
      r.last_key_fetch = new Date().toISOString().slice(0, 19);
      r.last_ip = clientIp(request);
      if (ver) r.app_version = ver;
      await env.NB_TOKENS.put(`tok:${tokenId}`, JSON.stringify(r));
      await env.NB_TOKENS.put(`ident:${r.ident}`, JSON.stringify(r));
    } catch {}
  })());

  return json({
    ok: true,
    key: env.OPENAI_KEY,
    key_id: (await sha256Hex(env.OPENAI_KEY)).slice(0, 12),
  });
}

// --------------------------------------------------------------------- proxy
async function handleProxy(request, env, ctx, path) {
  const token = bearer(request);
  if (!token) {
    return json({ error: { message: "missing NanoBanana token", code: "invalid_token" } }, 401);
  }
  const tokenId = (await sha256Hex(token)).slice(0, 16);
  const raw = await env.NB_TOKENS.get(`tok:${tokenId}`);
  if (!raw) {
    return json({ error: { message: "invalid or revoked NanoBanana token", code: "invalid_token" } }, 401);
  }
  const rec = JSON.parse(raw);
  if (rec.revoked || !timingSafeEqual(rec.token, token)) {
    return json({ error: { message: "invalid or revoked NanoBanana token", code: "invalid_token" } }, 401);
  }

  const headers = new Headers();
  headers.set("Authorization", "Bearer " + env.OPENAI_KEY);
  const ct = request.headers.get("Content-Type");
  if (ct) headers.set("Content-Type", ct);

  const isGet = request.method === "GET";
  const init = { method: request.method, headers };
  if (!isGet) {
    init.body = request.body;
    // Required whenever the body is a stream rather than a buffer. Without it
    // the runtime refuses the request outright.
    init.duplex = "half";
  }
  const upstream = new Request(UPSTREAM + path, init);

  const res = await fetch(upstream);

  // Counting is bookkeeping — it must not delay the image on its way back.
  ctx.waitUntil((async () => {
    try {
      const cur = await env.NB_TOKENS.get(`tok:${tokenId}`);
      if (!cur) return;
      const r = JSON.parse(cur);
      r.calls = (r.calls || 0) + 1;
      r.last_used = new Date().toISOString().slice(0, 19);
      await env.NB_TOKENS.put(`tok:${tokenId}`, JSON.stringify(r));
      await env.NB_TOKENS.put(`ident:${r.ident}`, JSON.stringify(r));
    } catch {}
  })());

  // Stream the body rather than reading it: a 15MB image never lands in memory
  // and the CPU budget stays untouched.
  const out = new Headers();
  const passthrough = ["content-type", "content-length", "x-request-id"];
  for (const k of passthrough) {
    const v = res.headers.get(k);
    if (v) out.set(k, v);
  }
  return new Response(res.body, { status: res.status, headers: out });
}

// --------------------------------------------------------------------- admin
async function handleAdmin(request, env, path) {
  if (!(await adminOk(request, env))) {
    return json({ ok: false, error: "admin key required" }, 403);
  }
  if (path === "/admin/tokens") {
    const list = await env.NB_TOKENS.list({ prefix: "tok:" });
    const rows = [];
    for (const k of list.keys) {
      const v = await env.NB_TOKENS.get(k.name);
      if (!v) continue;
      const r = JSON.parse(v);
      rows.push({
        token_id: r.token_id, user: r.user, machine: r.machine,
        issued_at: r.issued_at, last_used: r.last_used || null,
        calls: r.calls || 0, revoked: Boolean(r.revoked),
        app_version: r.app_version || "",
        key_fetches: r.key_fetches || 0, last_key_fetch: r.last_key_fetch || null,
        last_ip: r.last_ip || null,
      });
    }
    rows.sort((a, b) => (a.issued_at < b.issued_at ? -1 : 1));
    return json({ ok: true, count: rows.length, tokens: rows });
  }
  if (path === "/admin/revoke" && request.method === "POST") {
    let body;
    try { body = await request.json(); } catch { return json({ ok: false, error: "bad request" }, 400); }
    const key = `tok:${body.token_id}`;
    const v = await env.NB_TOKENS.get(key);
    if (!v) return json({ ok: false, error: "no such token" }, 404);
    const r = JSON.parse(v);
    r.revoked = true;
    r.revoked_at = new Date().toISOString().slice(0, 19);
    await env.NB_TOKENS.put(key, JSON.stringify(r));
    await env.NB_TOKENS.put(`ident:${r.ident}`, JSON.stringify(r));
    return json({ ok: true, token_id: body.token_id });
  }
  return json({ ok: false, error: "not found" }, 404);
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;

    // Nothing here says who this belongs to or what it proxies.
    if (path === "/health") return json({ ok: true });
    if (path === "/") return json({ ok: true }, 404);

    if (path === "/enroll" && request.method === "POST") {
      return handleEnroll(request, env);
    }
    if (path === "/key" && request.method === "POST") {
      return handleKey(request, env, ctx);
    }
    if (path.startsWith("/admin/")) {
      return handleAdmin(request, env, path);
    }
    if (ALLOWED.has(path)) {
      return handleProxy(request, env, ctx, path);
    }
    return json({ error: { message: "path not allowed: " + path } }, 404);
  },
};
