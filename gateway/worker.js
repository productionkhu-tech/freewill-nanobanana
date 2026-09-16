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

/**
 * Bearer 토큰 하나를 검사해 그 토큰의 등록 기록을 돌려준다.
 *
 * /key 가 인라인으로 하던 것과 같은 검사다. /usage 와 /catalog 도 같은 문을
 * 써야 하는데, 검사를 복사해두면 한쪽만 고쳐지는 날이 온다. 사용량 행에
 * 붙는 user/machine 도 여기서 나온다 — 앱이 자기 이름을 주장하게 두면
 * 남의 팀에 비용을 떠넘길 수 있으니, 신원은 토큰에서만 읽는다.
 */
async function tokenRecord(request, env) {
  const token = bearer(request);
  if (!token) return { ok: false, status: 401, error: "token required" };
  const tokenId = (await sha256Hex(token)).slice(0, 16);
  const raw = await env.NB_TOKENS.get(`tok:${tokenId}`);
  if (!raw) return { ok: false, status: 401, error: "invalid token" };
  const rec = JSON.parse(raw);
  if (rec.revoked || !timingSafeEqual(rec.token, token)) {
    return { ok: false, status: 401, error: "revoked token" };
  }
  return { ok: true, rec, tokenId };
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
  // ---- 사용량 조회 / 설정 (전부 관리자 키) --------------------------------
  if (path === "/admin" || path === "/admin/") {
    return new Response(ADMIN_HTML, {
      headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
  }
  if (path === "/admin/fx") {
    if (new URL(request.url).searchParams.get("refresh") === "1") {
      const r = await fetchFxRate(env);
      if (!r.ok) return json({ ok: false, error: r.error });
    }
    const cur = await latestFx(env);
    return json({ ok: true, day: cur && cur.day, usd_krw: cur && cur.usd_krw });
  }
  if (path === "/admin/usage") {
    return json(await handleAdminUsage(new URL(request.url), env));
  }
  if (path === "/admin/catalog") {
    return json(await handleCatalog(env, true));   // 보관된 것까지 (되살리기용)
  }
  /**
   * "지금 잘 돌아가고 있나" 한 줄.
   *
   * 집계가 조용히 멈추는 경우가 진짜 문제다 — 화면엔 숫자가 그대로 떠 있어서
   * 며칠 지나서야 알아챈다. 그래서 (1) 시트 동기화가 마지막으로 돈 시각과
   * (2) 사용량이 마지막으로 들어온 시각을 같이 보여준다.
   * 시각 비교는 서버 시간(now)을 같이 내려 클라이언트 시계와 무관하게 한다.
   */
  if (path === "/admin/health") {
    const now = nowIso();
    const today = now.slice(0, 10);
    const sync = await env.USAGE_DB.prepare(
      "SELECT at, ok, detail, changed_at FROM sync_state WHERE id='sheet'").first();
    // day 인덱스로 최근 며칠만 훑는다. created_at 에는 인덱스가 없어서
    // 전체 정렬을 걸면 행이 쌓일수록 조회 비용이 그대로 커진다.
    const recent = await env.USAGE_DB.prepare(
      `SELECT MAX(created_at) AS last_at FROM usage_events
        WHERE day >= date('now','-7 day')`).first();
    const td = await env.USAGE_DB.prepare(
      `SELECT COUNT(*) AS rows_n, COALESCE(SUM(images),0) AS images
         FROM usage_events WHERE day = ?`).bind(today).first();
    const fx = await latestFx(env);
    return json({
      ok: true, now,
      sync: sync || null,
      usage: { last_at: (recent && recent.last_at) || null,
               today_images: (td && td.images) || 0,
               today_rows: (td && td.rows_n) || 0 },
      fx: fx ? { day: fx.day, usd_krw: fx.usd_krw } : null,
    });
  }
  // 구글 시트에서 팀/프로젝트를 끌어온다. 크론이 하루 한 번 돌지만, 시트를
  // 방금 고치고 바로 반영하고 싶을 때가 있어서 버튼도 둔다.
  if (path === "/admin/sync") {
    if (request.method === "POST") {
      // 버튼으로 부른 건 force — 내용이 그대로여도 목록을 시트대로 다시 맞춘다.
      let b = {};
      try { b = await request.json(); } catch {}
      return json(await syncSheetsSafe(env, b.force !== false));
    }
    const r = await env.USAGE_DB.prepare(
      "SELECT at, ok, detail, changed_at FROM sync_state WHERE id='sheet'").first();
    return json({ ok: true, last: r || null });
  }
  // 팀·프로젝트 추가/수정. active=0 은 삭제가 아니라 **보관**이다 — 앱 목록에서만
  // 사라지고 과거 사용량 행과 이름은 그대로 남아 리포트에 계속 나온다.
  // 끝난 프로젝트의 지난 비용이 안 보이면 그건 집계가 아니라 구멍이다.
  if (path === "/admin/team" && request.method === "POST") {
    let b; try { b = await request.json(); } catch { return json({ ok: false, error: "bad request" }, 400); }
    const id = slug(b.id || b.name);
    if (!id) return json({ ok: false, error: "name required" }, 400);
    if (b.delete) return json(await dropCatalogRow(env, "teams", "team_id", id));
    await env.USAGE_DB.prepare(
      `INSERT INTO teams (id, name, active, created_at) VALUES (?,?,?,?)
       ON CONFLICT(id) DO UPDATE SET name=excluded.name, active=excluded.active`)
      .bind(id, String(b.name || id).slice(0, 64), b.active === false ? 0 : 1, nowIso()).run();
    return json({ ok: true, id });
  }
  if (path === "/admin/project" && request.method === "POST") {
    let b; try { b = await request.json(); } catch { return json({ ok: false, error: "bad request" }, 400); }
    const id = slug(b.id || b.name);
    if (!id) return json({ ok: false, error: "name required" }, 400);
    if (b.delete) return json(await dropCatalogRow(env, "projects", "project_id", id));
    // 팀은 선택이다. 한 건을 여러 팀이 같이 하는 게 정상이라, 프로젝트에 팀을
    // 하나 박으면 실제로 작업한 팀이 아닌 쪽으로 비용이 잡힌다.
    const teamId = b.team_id ? slug(b.team_id) : null;
    await env.USAGE_DB.prepare(
      `INSERT INTO projects (id, name, team_id, active, created_at) VALUES (?,?,?,?,?)
       ON CONFLICT(id) DO UPDATE SET name=excluded.name, team_id=excluded.team_id, active=excluded.active`)
      .bind(id, String(b.name || id).slice(0, 64), teamId, b.active === false ? 0 : 1, nowIso()).run();
    return json({ ok: true, id });
  }
  if (path === "/admin/prices") {
    if (request.method === "POST") {
      let b; try { b = await request.json(); } catch { return json({ ok: false, error: "bad request" }, 400); }
      if (!b.model) return json({ ok: false, error: "model required" }, 400);
      const model = String(b.model).slice(0, 64);
      const day = (s) => (/^\d{4}-\d{2}-\d{2}$/.test(String(s || "")) ? String(s) : null);

      // 직전 행을 기준으로 삼는다 — provider 같은 건 매번 다시 받을 이유가 없다.
      const prev = await env.USAGE_DB.prepare(
        "SELECT * FROM prices WHERE model=? ORDER BY effective_from DESC LIMIT 1")
        .bind(model).first();

      // effective_from 을 주면 그 시점 행을 만들거나 고친다. 안 주면 가장 최근
      // 행을 고친다(오타 정정). **model 만 보고 UPDATE 하면 과거 시점 행까지
      // 같이 덮여 지난달 집계가 조용히 바뀐다** — 예전 코드가 그랬다.
      const eff = day(b.effective_from) || (prev ? prev.effective_from : "2000-01-01");
      const num = (v, d) => (v === undefined || v === null || v === "" ? (d || 0) : Number(v) || 0);
      await env.USAGE_DB.prepare(
        `INSERT INTO prices
           (model, effective_from, provider, mode, in_text_per_m, in_image_per_m, out_per_m,
            per_image, per_image_hi, px_threshold, in_per_image, in_free_count,
            verified, note, updated_at)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
         ON CONFLICT(model, effective_from) DO UPDATE SET
           provider=excluded.provider, mode=excluded.mode,
           in_text_per_m=excluded.in_text_per_m, in_image_per_m=excluded.in_image_per_m,
           out_per_m=excluded.out_per_m, per_image=excluded.per_image,
           per_image_hi=excluded.per_image_hi, px_threshold=excluded.px_threshold,
           in_per_image=excluded.in_per_image, in_free_count=excluded.in_free_count,
           verified=excluded.verified, note=excluded.note, updated_at=excluded.updated_at`)
        .bind(model, eff,
              String(b.provider || (prev && prev.provider) || "").slice(0, 24),
              String(b.mode || (prev && prev.mode) || "token"),
              num(b.in_text_per_m, prev && prev.in_text_per_m),
              num(b.in_image_per_m, prev && prev.in_image_per_m),
              num(b.out_per_m, prev && prev.out_per_m),
              num(b.per_image, prev && prev.per_image),
              // 고화소 단가와 레퍼런스 과금도 같이 저장한다. 예전엔 화면에 칸만
              // 있고 저장이 안 돼 고쳐도 조용히 원래 값으로 남았다.
              num(b.per_image_hi, prev && prev.per_image_hi),
              num(b.px_threshold, prev && prev.px_threshold),
              num(b.in_per_image, prev && prev.in_per_image),
              num(b.in_free_count, prev && prev.in_free_count),
              b.verified ? 1 : 0, String(b.note || "").slice(0, 200), nowIso()).run();
      return json({ ok: true, model, effective_from: eff });
    }
    const r = await env.USAGE_DB.prepare(
      "SELECT * FROM prices ORDER BY provider, model, effective_from").all();
    return json({ ok: true, prices: r.results || [] });
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

// ---------------------------------------------------------------- usage / D1
//
// 왜 워커가 이걸 떠안는가: 게이트웨이는 키만 나눠주고 생성은 앱이 직접 한다
// (속도 때문에 그렇게 정했다). 그래서 워커는 생성 트래픽을 볼 수 없고,
// 사용량은 앱이 사후 보고하는 수밖에 없다. 이 파일은 그 수집구와 조회구다.
//
// 금액은 저장하지 않는다. 프로바이더가 준 원본 사용량만 적고 단가는 조회할 때
// 곱한다 — 단가가 바뀌거나 잘못 넣었을 때 과거까지 한 번에 바로잡는 유일한 길이다.

const nowIso = () => new Date().toISOString().slice(0, 19);
const slug = (s) => String(s || "").trim().toLowerCase()
  .replace(/[^a-z0-9가-힣]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);

/**
 * 프로젝트 id 는 **이름이 아니라 건 번호**로 만든다.
 *
 * 이름은 바뀐다 ("[26P52]...FW" -> "[26P52]...FW 수정"). id 를 이름 슬러그로
 * 잡으면 시트에서 한 글자만 고쳐도 다른 프로젝트가 되어버려, 그때까지 쌓인
 * 비용이 옛 id 에 고아로 남는다. 대괄호 안 번호는 안 바뀌므로 그걸 쓴다.
 * 번호가 없는 건(TA Test 같은)만 이름 슬러그로 떨어진다.
 */
function projectKey(name) {
  const m = String(name || "").match(/^\s*\[([A-Za-z0-9_\-]{2,20})\]/);
  return m ? m[1].toLowerCase() : slug(name);
}

// ----------------------------------------------------------- Google Sheets
// 팀과 프로젝트 목록의 원본은 사용자가 매일 쓰는 구글 시트다. 관리자 페이지에서
// 또 한 벌 관리하게 두면 둘이 어긋나고, 어긋나면 비용이 엉뚱한 데 붙는다.

const b64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes)))
  .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

/**
 * 서비스 계정 JWT -> 액세스 토큰. 워커에 구글 SDK 를 넣을 수 없으니 직접 만든다.
 * 토큰은 1시간짜리라 KV 에 담아 재사용한다 — 1분마다 도는 동기화가 매번 새로
 * 받으면 그만큼 느리고 구글 쪽 호출도 60배가 된다.
 */
async function googleToken(env) {
  try {
    const c = await env.NB_TOKENS.get("gs:token", "json");
    if (c && c.exp > Date.now() / 1000 + 120) return c.token;
  } catch {}
  const tok = await googleTokenFresh(env);
  try {
    await env.NB_TOKENS.put("gs:token",
      JSON.stringify({ token: tok, exp: Math.floor(Date.now() / 1000) + 3300 }),
      { expirationTtl: 3300 });
  } catch {}
  return tok;
}

async function googleTokenFresh(env) {
  const pem = String(env.GS_SA_KEY || "").replace(/\\n/g, "\n");
  const body = pem.replace(/-----(BEGIN|END) PRIVATE KEY-----/g, "").replace(/\s+/g, "");
  if (!body || !env.GS_SA_EMAIL) throw new Error("sheet credentials not configured");
  const der = Uint8Array.from(atob(body), (c) => c.charCodeAt(0));
  const key = await crypto.subtle.importKey(
    "pkcs8", der, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"]);

  const now = Math.floor(Date.now() / 1000);
  const enc = new TextEncoder();
  const unsigned = b64url(enc.encode(JSON.stringify({ alg: "RS256", typ: "JWT" }))) + "." +
    b64url(enc.encode(JSON.stringify({
      iss: env.GS_SA_EMAIL,
      scope: "https://www.googleapis.com/auth/spreadsheets.readonly",
      aud: "https://oauth2.googleapis.com/token",
      iat: now, exp: now + 3600,
    })));
  const sig = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, enc.encode(unsigned));
  const assertion = unsigned + "." + b64url(sig);

  const r = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: "grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion=" +
          encodeURIComponent(assertion),
  });
  const d = await r.json();
  if (!d.access_token) throw new Error("google auth failed: " + JSON.stringify(d).slice(0, 160));
  return d.access_token;
}

/**
 * 시트 -> D1 동기화.
 *
 * 지우지 않는다. 시트에서 빠지거나 '종료' 로 바뀐 건은 **보관**(active=0)으로
 * 내려갈 뿐이고, 그 건에 쌓인 사용량과 이름표는 리포트에 그대로 남는다.
 * 그리고 시트가 만든 행(source='sheet')만 건드린다 — 관리자 페이지에서 손으로
 * 추가한 건 시트에 없다고 해서 조용히 내려가면 안 된다.
 */
async function syncSheets(env, force) {
  const token = await googleToken(env);
  const sid = env.GS_SHEET_ID;
  if (!sid) throw new Error("GS_SHEET_ID not set");
  const url = "https://sheets.googleapis.com/v4/spreadsheets/" + sid +
    "/values:batchGet?ranges=" + encodeURIComponent("Project_Status!A2:C500") +
    "&ranges=" + encodeURIComponent("config_teams!A2:A200");
  const r = await fetch(url, { headers: { Authorization: "Bearer " + token } });
  const d = await r.json();
  if (!d.valueRanges) throw new Error("sheet read failed: " + JSON.stringify(d).slice(0, 160));

  const projRows = d.valueRanges[0].values || [];
  const teamRows = d.valueRanges[1].values || [];
  const now = nowIso();

  // 시트가 그대로면 D1 에 아무것도 쓰지 않는다. 1분마다 38행씩 덮어쓰면
  // 하루 5만 건이라 무료 쓰기 한도를 그냥 태운다 — 바뀐 날만 쓰면 사실상 0 이다.
  const stamp = (await sha256Hex(JSON.stringify([teamRows, projRows]))).slice(0, 32);
  const prev = await env.USAGE_DB.prepare(
    "SELECT hash, detail, changed_at FROM sync_state WHERE id='sheet'").first();
  // 크론은 안 바뀌었으면 넘어가지만, 사람이 버튼을 누른 경우엔 항상 맞춘다.
  // 시트가 그대로여도 D1 쪽이 틀어져 있을 수 있고(손으로 잘못 보관했다거나),
  // 그걸 되돌릴 방법이 해시 건너뛰기 때문에 없어지면 안 된다.
  if (!force && prev && prev.hash === stamp) {
    await env.USAGE_DB.prepare(
      "UPDATE sync_state SET at=?, ok=1 WHERE id='sheet'").bind(now).run();
    return { ok: true, at: now, unchanged: true, detail: prev.detail || "",
             changed_at: prev.changed_at || null };
  }

  const writes = [];

  // 팀: 이름 한 칸이 전부다. 시트에 있으면 살아 있는 것.
  const teamIds = [];
  const teamStmt = env.USAGE_DB.prepare(
    `INSERT INTO teams (id, name, active, created_at, source) VALUES (?,?,1,?,'sheet')
     ON CONFLICT(id) DO UPDATE SET name=excluded.name, active=1, source='sheet'`);
  for (const row of teamRows) {
    const name = String((row[0] || "")).trim();
    if (!name) continue;
    const id = slug(name);
    if (!id || teamIds.includes(id)) continue;
    teamIds.push(id);
    writes.push(teamStmt.bind(id, name.slice(0, 64), now));
  }

  // 프로젝트: B=이름, C=진행현황. '진행' 만 앱 목록에 보인다.
  const projIds = [];
  let live = 0;
  const projStmt = env.USAGE_DB.prepare(
    `INSERT INTO projects (id, name, team_id, active, created_at, source)
     VALUES (?,?,NULL,?,?,'sheet')
     ON CONFLICT(id) DO UPDATE SET name=excluded.name, active=excluded.active, source='sheet'`);
  for (const row of projRows) {
    const name = String((row[1] || "")).trim();
    if (!name) continue;
    const id = projectKey(name);
    if (!id || projIds.includes(id)) continue;
    const active = String(row[2] || "").trim() === "진행" ? 1 : 0;
    if (active) live++;
    projIds.push(id);
    writes.push(projStmt.bind(id, name.slice(0, 64), active, now));
  }
  if (!teamIds.length && !projIds.length) throw new Error("sheet looked empty - refusing to sync");

  // 시트에서 통째로 사라진 건은 보관으로 내린다 (삭제가 아니다).
  const ph = (n) => new Array(n).fill("?").join(",");
  if (teamIds.length) {
    writes.push(env.USAGE_DB.prepare(
      `UPDATE teams SET active=0 WHERE source='sheet' AND id NOT IN (${ph(teamIds.length)})`)
      .bind(...teamIds));
  }
  if (projIds.length) {
    writes.push(env.USAGE_DB.prepare(
      `UPDATE projects SET active=0 WHERE source='sheet' AND id NOT IN (${ph(projIds.length)})`)
      .bind(...projIds));
  }
  await env.USAGE_DB.batch(writes);

  const detail = "팀 " + teamIds.length + " · 프로젝트 " + live + " 진행 / " +
                 (projIds.length - live) + " 보관";
  await env.USAGE_DB.prepare(
    `INSERT INTO sync_state (id, at, ok, detail, hash, changed_at)
     VALUES ('sheet',?,1,?,?,?)
     ON CONFLICT(id) DO UPDATE SET at=excluded.at, ok=1, detail=excluded.detail,
                                   hash=excluded.hash, changed_at=excluded.changed_at`)
    .bind(now, detail, stamp, now).run();
  return { ok: true, at: now, changed_at: now, teams: teamIds.length,
           projects: projIds.length, live, detail };
}

async function syncSheetsSafe(env, force) {
  try {
    return await syncSheets(env, force);
  } catch (e) {
    const msg = String(e && e.message || e).slice(0, 200);
    try {
      await env.USAGE_DB.prepare(
        `INSERT INTO sync_state (id, at, ok, detail) VALUES ('sheet',?,0,?)
         ON CONFLICT(id) DO UPDATE SET at=excluded.at, ok=0, detail=excluded.detail`)
        .bind(nowIso(), msg).run();
    } catch {}
    return { ok: false, error: msg };
  }
}

// 관리자 페이지. 워커가 직접 서빙하므로 호스팅 비용이 없다.
// 로그인은 관리자 키 하나 — 계정 시스템을 붙일 이유가 없는 1인용 화면이다.
const ADMIN_HTML = `<!DOCTYPE html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NanoBanana 사용량</title><link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%231e1e23'/%3E%3Crect x='7' y='17' width='4.5' height='9' rx='1.5' fill='%23D4A574'/%3E%3Crect x='13.8' y='12' width='4.5' height='14' rx='1.5' fill='%23D4A574'/%3E%3Crect x='20.6' y='6' width='4.5' height='20' rx='1.5' fill='%23E8C9A0'/%3E%3C/svg%3E"><style>
:root{--bg:#16161a;--surf:#1e1e23;--surf2:#25252b;--line:#2e2e36;--tx:#e9e9ee;--tx2:#9a9aa4;
      --acc:#D4A574;--acc2:#E8C9A0;--warn:#FFD60A;--ok:#7ee08a;--err:#ff8a8a}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font:13px/1.55 'Malgun Gothic','Segoe UI',system-ui,sans-serif;
     padding:22px;max-width:1180px;margin:0 auto}
h1{font-size:18px;letter-spacing:-.2px}
.top{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:3px}
.sub{color:var(--tx2);font-size:12px}
.tabs{display:flex;gap:4px;margin:16px 0 14px;border-bottom:1px solid var(--line)}
.tabs button{background:none;border:none;color:var(--tx2);padding:9px 15px;cursor:pointer;font:inherit;
             border-bottom:2px solid transparent}
.tabs button.on{color:var(--tx);border-bottom-color:var(--acc)}
.card{background:var(--surf);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:14px}
.card h2{font-size:13px;color:var(--tx2);font-weight:600;margin-bottom:12px;letter-spacing:.2px}
.row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
label{color:var(--tx2);font-size:12px}
input,select{background:#121216;color:var(--tx);border:1px solid var(--line);border-radius:7px;
             padding:6px 9px;font:inherit}
button.go{background:var(--acc);color:#221a10;border:none;border-radius:7px;padding:7px 15px;
          font-weight:700;cursor:pointer}
button.ghost{background:var(--surf2);color:var(--tx);border:1px solid var(--line);border-radius:7px;
             padding:5px 11px;cursor:pointer;font:inherit;white-space:nowrap}
button.ghost:hover{border-color:var(--acc)}
.chips{display:flex;gap:5px;flex-wrap:wrap}
.chips button{background:none;border:1px solid var(--line);color:var(--tx2);border-radius:999px;
              padding:5px 12px;cursor:pointer;font:inherit;font-size:12px}
.chips button.on{background:var(--acc);border-color:var(--acc);color:#221a10;font-weight:700}

/* 기간을 문장으로 크게 — 이 화면을 남과 같이 볼 때 제일 먼저 물어보는 게 "언제부터?" 다 */
.period{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.period .range{font-size:20px;font-weight:700;letter-spacing:-.3px}
.period .len{color:var(--tx2);font-size:12px}

.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:14px}
.kpi{background:var(--surf);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.kpi .k{color:var(--tx2);font-size:12px;margin-bottom:5px}
.kpi .v{font-size:25px;font-weight:700;letter-spacing:-.5px;line-height:1.15}
.kpi .s{color:var(--tx2);font-size:12px;margin-top:3px}
.kpi.flag{border-color:#5a4a12}
.kpi.flag .v{color:var(--warn)}

table{width:100%;border-collapse:collapse;margin-top:4px}
th,td{text-align:left;padding:8px 9px;border-bottom:1px solid var(--line);font-size:12px}
th{color:var(--tx2);font-weight:600}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
tfoot td{font-weight:700;border-top:1px solid var(--line)}
.pill{display:inline-block;background:#3a2f12;color:var(--warn);border-radius:10px;padding:1px 8px;font-size:11px}
.pill.arch{background:#2b2b33;color:var(--tx2)}
.muted{color:var(--tx2)}.err{color:var(--err)}.ok{color:var(--ok)}
.bar{height:7px;background:var(--surf2);border-radius:4px;overflow:hidden;min-width:60px}
.bar > i{display:block;height:100%;background:var(--acc);border-radius:4px}
.empty{color:var(--tx2);padding:18px 2px}
svg.chart{width:100%;height:160px;display:block}
svg.chart .gl{stroke:var(--line);stroke-width:1}
svg.chart .bb{fill:var(--acc);opacity:.85}
svg.chart .bb:hover{opacity:1;fill:var(--acc2)}
svg.chart text{fill:var(--tx2);font-size:10px}
.legend{color:var(--tx2);font-size:11px;margin-top:6px}
/* 지금 잘 돌고 있나 — 숫자는 멈춰도 그대로 떠 있어서 따로 알려줘야 한다 */
.health{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.hs{display:flex;align-items:center;gap:8px;background:var(--surf);border:1px solid var(--line);
    border-radius:999px;padding:6px 14px;font-size:12px}
.hs b{font-weight:600}
.hs .dot{width:8px;height:8px;border-radius:50%;background:var(--ok);flex:none}
.hs.warn{border-color:#5a4a12}.hs.warn .dot{background:var(--warn)}
.hs.bad{border-color:#5a2020}.hs.bad .dot{background:var(--err)}
.hs .when{color:var(--tx2)}
.split{display:grid;grid-template-columns:1fr 1fr;gap:14px}
/* 단가표는 칸이 많다 — 접어놓으면 읽을 수 없으니 가로로 밀어서 본다. */
#priceTable{overflow-x:auto}
#priceTable table{min-width:940px}
#priceTable td,#priceTable th{white-space:nowrap}
#priceTable input{width:62px;padding:4px 6px}
#priceTable select{padding:4px 6px}
/* 모델명은 한 줄로, 설명만 접힌다 */
#priceTable td:first-child{white-space:nowrap}
#priceTable td:first-child div{white-space:normal;max-width:240px}
/* 이름이 길어도 ID·버튼 칸이 짜불어지지 않게 */
#teamList td:first-child,#projList td:first-child{word-break:keep-all}
#teamList td:nth-child(2),#projList td:nth-child(2){font-size:11px;max-width:150px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#teamList td:last-child,#projList td:last-child{width:1%;white-space:nowrap}
@media(max-width:860px){.split{grid-template-columns:1fr}}
</style></head><body>

<div class="top"><h1>NanoBanana 사용량</h1><span class="sub" id="fxLine"></span></div>
<div class="sub">이미지 한 장마다 팀·프로젝트로 비용이 잡힙니다. 금액은 저장하지 않고, 조회할 때 그 날짜의 단가와 환율을 곱합니다.</div>

<div class="tabs">
  <button id="tb-usage" class="on" onclick="show('usage')">사용량</button>
  <button id="tb-setup" onclick="show('setup')">팀 · 프로젝트</button>
  <button id="tb-price" onclick="show('price')">단가 · 환율</button>
</div>

<div id="pane-usage">
  <div class="card">
    <div class="row" style="justify-content:space-between">
      <div class="chips" id="presets"></div>
      <div class="row">
        <input type="date" id="from"><span class="muted">~</span><input type="date" id="to">
        <button class="go" onclick="loadUsage()">조회</button>
      </div>
    </div>
  </div>

  <div class="health" id="health"></div>

  <div class="period"><span class="range" id="periodText">—</span><span class="len" id="periodLen"></span></div>
  <div class="kpis" id="kpis"></div>

  <div class="card">
    <h2>날짜별 추이</h2>
    <div id="trend"><div class="empty">불러오는 중…</div></div>
  </div>

  <div class="card">
    <div class="row" style="justify-content:space-between;margin-bottom:10px">
      <h2 style="margin:0">묶어 보기</h2>
      <div class="chips" id="gbChips"></div>
    </div>
    <div id="shareChart"></div>
    <div id="usageTable"><div class="empty">조회를 눌러주세요.</div></div>
  </div>
</div>

<div id="pane-setup" style="display:none">
  <div class="card">
    <div class="row" style="justify-content:space-between">
      <div>
        <b>구글 시트가 원본입니다</b>
        <div class="sub" style="margin-top:4px" id="syncLast">불러오는 중…</div>
      </div>
      <button class="go" onclick="runSync()" id="syncBtn">지금 동기화</button>
    </div>
    <div class="sub" style="margin-top:10px;line-height:1.7">
      Project_Status 시트의 <b>진행</b>인 건만 앱 목록에 보입니다. <b>종료</b>로 바꾸거나 줄을 지우면
      다음 동기화 때 보관으로 내려가고, <b>그 건에 쌓인 비용은 리포트에 그대로 남습니다.</b><br>
      <b>1분마다</b> 자동으로 확인하므로 일과 중에 시트를 고치셔도 곧 반영됩니다. 급하면 위 버튼으로 즉시.
      읽기 전용으로만 연결돼 있어 이쪽에서 시트를 고치는 일은 없습니다.
      아래에서 손으로 추가한 항목(<span class="pill arch">직접</span>)은 동기화가 건드리지 않습니다.
    </div>
  </div>
  <div class="split">
    <div class="card">
      <h2>팀</h2>
      <div class="row">
        <input id="tName" placeholder="예: 11팀" style="flex:1;min-width:140px">
        <button class="go" onclick="addTeam()">추가</button>
      </div>
      <div id="teamList"></div>
    </div>
    <div class="card">
      <h2>프로젝트</h2>
      <div class="row">
        <input id="pName" placeholder="예: [26P53]○○○ 캠페인" style="flex:1;min-width:160px">
        <button class="go" onclick="addProject()">추가</button>
      </div>
      <div id="projList"></div>
    </div>
  </div>
  <div class="card sub" style="line-height:1.7">
    팀과 프로젝트는 각각 따로 고릅니다 &mdash; 한 건을 여러 팀이 같이 할 수 있기 때문입니다.<br>
    <b>보관</b>은 삭제가 아닙니다. 앱의 선택 목록에서만 빠지고 <b>지난 사용량과 비용은 리포트에 그대로 남습니다.</b>
    끝난 프로젝트는 보관해 두면 목록이 깔끔해지고, 나중에 다시 쓸 일이 생기면 되살리면 됩니다.
  </div>
</div>

<div id="pane-price" style="display:none">
  <div class="card">
    <h2>환율</h2>
    <div class="row"><span id="fxNow" class="muted"></span>
      <button class="ghost" onclick="refreshFx()">지금 갱신</button></div>
    <div class="sub" style="margin-top:8px">매일 자동으로 받아옵니다. 리포트는 각 이미지가 만들어진 <b>그 날의 환율</b>로 환산합니다 &mdash; 오늘 환율로 과거를 환산하면 지난달 숫자가 매일 달라집니다.</div>
  </div>
  <div class="card"><h2>모델 단가 (USD)</h2><div id="priceTable"></div>
    <div class="sub" style="margin-top:10px">단가를 고치면 <b>과거 집계까지 다시 계산</b>됩니다. 금액이 아니라 사용량을 저장하기 때문입니다.<br>
      장당가는 출력 픽셀이 기준을 넘으면 고화소 단가로 바뀝니다 (Seedream 5 Pro: 2.61M 픽셀 초과).</div>
  </div>
</div>

<script>
const KEY_LS = "nb_admin_key";
let KEY = localStorage.getItem(KEY_LS) || "";
if (!KEY) { KEY = prompt("관리자 키") || ""; if (KEY) localStorage.setItem(KEY_LS, KEY); }
const H = () => ({ "X-Admin-Key": KEY, "Content-Type": "application/json" });

const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = n => (Number(n) || 0).toLocaleString("ko-KR");
const krw = n => nf(Math.round(Number(n) || 0)) + "원";
// 작은 금액이 전부 $0.00 으로 보이면 아무것도 못 읽는다 — 자리수를 값에 맞춘다.
const usd = n => { const v = Number(n) || 0;
  return "$" + (v === 0 ? "0" : v < 1 ? v.toFixed(4) : v < 1000 ? v.toFixed(2) : nf(Math.round(v))); };
const pct = (a, b) => b > 0 ? (a / b * 100).toFixed(1) + "%" : "0%";
// 2장/16일 을 반올림해 "0장" 으로 보여주면 숫자가 거짓말을 한다.
const avg = n => { const v = Number(n) || 0;
  return v > 0 && v < 10 ? v.toFixed(1) : nf(Math.round(v)); };

async function api(path, opt) {
  const r = await fetch(path, { headers: H(), ...(opt || {}) });
  const d = await r.json().catch(() => ({}));
  if (r.status === 403) {
    localStorage.removeItem(KEY_LS);
    alert("관리자 키가 틀렸습니다. 새로고침 후 다시 입력하세요.");
  }
  return d;
}
function show(which) {
  ["usage", "setup", "price"].forEach(k => {
    document.getElementById("pane-" + k).style.display = (k === which) ? "" : "none";
    document.getElementById("tb-" + k).className = (k === which) ? "on" : "";
  });
  if (which === "setup") { loadCatalog(); loadSync(); }
  if (which === "price") { loadPrices(); loadFx(); }
}

/* ---- 기간 ---- */
const iso = d => d.toISOString().slice(0, 10);
function presetRange(k) {
  const now = new Date(), y = now.getFullYear(), m = now.getMonth();
  if (k === "thisMonth") return [iso(new Date(Date.UTC(y, m, 1))), iso(now)];
  if (k === "lastMonth") return [iso(new Date(Date.UTC(y, m - 1, 1))), iso(new Date(Date.UTC(y, m, 0)))];
  if (k === "d7")  { const s = new Date(now); s.setDate(s.getDate() - 6); return [iso(s), iso(now)]; }
  if (k === "d30") { const s = new Date(now); s.setDate(s.getDate() - 29); return [iso(s), iso(now)]; }
  if (k === "year") return [iso(new Date(Date.UTC(y, 0, 1))), iso(now)];
  return ["2000-01-01", iso(now)];
}
const PRESETS = [["thisMonth", "이번 달"], ["lastMonth", "지난달"], ["d7", "최근 7일"],
                 ["d30", "최근 30일"], ["year", "올해"], ["all", "전체"]];
let curPreset = "thisMonth";
document.getElementById("presets").innerHTML = PRESETS.map(p =>
  '<button data-preset="' + p[0] + '">' + p[1] + '</button>').join("");
function applyPreset(k) {
  curPreset = k;
  const r = presetRange(k);
  document.getElementById("from").value = r[0];
  document.getElementById("to").value = r[1];
  markPreset();
  loadUsage();
}
function markPreset() {
  document.querySelectorAll("#presets button").forEach(b =>
    b.className = (b.dataset.preset === curPreset) ? "on" : "");
}
const KDATE = s => { const p = String(s).split("-");
  return p.length === 3 ? Number(p[0]) + "년 " + Number(p[1]) + "월 " + Number(p[2]) + "일" : s; };
function periodLabel(f, t) {
  document.getElementById("periodText").textContent =
    (f <= "2000-01-01" ? "전체 기간" : KDATE(f)) + " ~ " + KDATE(t);
  const days = Math.round((Date.parse(t) - Date.parse(f)) / 86400000) + 1;
  document.getElementById("periodLen").textContent =
    (f <= "2000-01-01" || !isFinite(days)) ? "" : "(" + nf(days) + "일간)";
  return days;
}

/* ---- 묶기 ---- */
const GBS = [["project", "프로젝트"], ["team", "팀"], ["user", "사람"],
             ["model", "모델"], ["machine", "PC"], ["day", "날짜"]];
let curGb = "project";
document.getElementById("gbChips").innerHTML = GBS.map(g =>
  '<button data-gb="' + g[0] + '">' + g[1] + '</button>').join("");
function markGb() {
  document.querySelectorAll("#gbChips button").forEach(b =>
    b.className = (b.dataset.gb === curGb) ? "on" : "");
}
const gbName = k => (GBS.find(g => g[0] === k) || [k, k])[1];

document.addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.preset) { applyPreset(b.dataset.preset); return; }
  if (b.dataset.gb) { curGb = b.dataset.gb; markGb(); loadUsage(); return; }
  if (b.dataset.offTeam) {
    await api("/admin/team", { method: "POST", body: JSON.stringify(
      { id: b.dataset.offTeam, name: b.dataset.name, active: false }) });
    loadCatalog();
  } else if (b.dataset.onTeam) {
    await api("/admin/team", { method: "POST", body: JSON.stringify(
      { id: b.dataset.onTeam, name: b.dataset.name, active: true }) });
    loadCatalog();
  } else if (b.dataset.offProj) {
    await api("/admin/project", { method: "POST", body: JSON.stringify(
      { id: b.dataset.offProj, name: b.dataset.name, active: false }) });
    loadCatalog();
  } else if (b.dataset.onProj) {
    await api("/admin/project", { method: "POST", body: JSON.stringify(
      { id: b.dataset.onProj, name: b.dataset.name, active: true }) });
    loadCatalog();
  } else if (b.dataset.delTeam || b.dataset.delProj) {
    const isTeam = Boolean(b.dataset.delTeam);
    const id = b.dataset.delTeam || b.dataset.delProj;
    if (!confirm('"' + b.dataset.name
               + '" 을(를) 목록에서 완전히 지울까요? 사용 기록이 한 건이라도 있으면 지워지지 않습니다.')) return;
    const d = await api(isTeam ? "/admin/team" : "/admin/project",
      { method: "POST", body: JSON.stringify({ id, name: b.dataset.name, delete: true }) });
    if (!d.ok) alert(d.error || "실패");
    loadCatalog();
  } else if (b.dataset.savePrice) {
    savePrice(b.dataset.savePrice, b.dataset.pid, b.dataset.eff);
  }
});

/* ---- 일별 막대 (라이브러리 없이 SVG 로 직접) ---- */
// 기록이 있는 날만 그리면 빈 날이 접혀 막대 간격이 날짜와 어긋난다 — 기간 전체를 깔아둔다.
// 너무 긴 구간(1년+)은 그대로 두면 막대가 머리카락이 되므로 있는 날만 보여준다.
function fillDays(from, to, rows) {
  const a = Date.parse(from), b = Date.parse(to);
  if (!isFinite(a) || !isFinite(b) || b < a) return rows;
  const n = Math.round((b - a) / 86400000) + 1;
  if (n > 120) return rows;
  const by = {};
  rows.forEach(r => { by[r.day] = r; });
  const out = [];
  for (let i = 0; i < n; i++) {
    const day = new Date(a + i * 86400000).toISOString().slice(0, 10);
    out.push(by[day] || { day, images: 0, cost_usd: 0, cost_krw: 0 });
  }
  return out;
}
function trendSvg(days, useKrw) {
  if (!days.length) return '<div class="empty">이 기간에 기록이 없습니다.</div>';
  const W = 1000, HH = 160, PB = 22, PL = 4;
  const val = d => useKrw ? (d.cost_krw || 0) : (d.cost_usd || 0);
  const max = Math.max.apply(null, days.map(val).concat([0])) || 1;
  const n = days.length, slot = (W - PL * 2) / n, bw = Math.max(2, Math.min(38, slot * 0.68));
  let s = '<svg class="chart" viewBox="0 0 ' + W + ' ' + HH + '" preserveAspectRatio="none">';
  for (let i = 0; i <= 3; i++) {
    const y = PB + (HH - PB * 2) * i / 3;
    s += '<line class="gl" x1="0" y1="' + y.toFixed(1) + '" x2="' + W + '" y2="' + y.toFixed(1) + '"/>';
  }
  days.forEach((d, i) => {
    const v = val(d), h = Math.max(1, (HH - PB * 2) * v / max);
    const x = PL + slot * i + (slot - bw) / 2, y = HH - PB - h;
    s += '<rect class="bb" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1)
       + '" height="' + h.toFixed(1) + '" rx="2"><title>' + esc(d.day) + '  '
       + (useKrw ? krw(d.cost_krw) : usd(d.cost_usd)) + '  ' + nf(d.images) + '장</title></rect>';
  });
  // 라벨은 첫·중간·끝만. 30일치 날짜를 다 찍으면 읽을 수 없는 띠가 된다.
  [0, Math.floor(n / 2), n - 1].filter((v, i, a) => a.indexOf(v) === i).forEach(i => {
    const x = PL + slot * i + slot / 2;
    s += '<text x="' + x.toFixed(1) + '" y="' + (HH - 6) + '" text-anchor="middle">'
       + esc(days[i].day.slice(5)) + '</text>';
  });
  s += '<text x="2" y="14">' + (useKrw ? krw(max) : usd(max)) + '</text>';
  return s + '</svg><div class="legend">막대 하나가 하루입니다. 올려두면 그 날 금액과 장수가 보입니다.</div>';
}

/* ---- 비중 막대 ---- */
function shareBars(rows, total, useKrw) {
  if (!rows.length) return "";
  const val = r => useKrw ? (r.cost_krw || 0) : (r.cost_usd || 0);
  const top = rows.slice(0, 8);
  const rest = rows.slice(8).reduce((a, r) => a + val(r), 0);
  let h = '<table style="margin-bottom:14px">';
  top.forEach(r => {
    const v = val(r);
    h += '<tr><td style="width:34%">' + esc(r.label)
       + (r.archived ? ' <span class="pill arch">보관</span>' : '')
       + '</td><td><div class="bar"><i style="width:' + (total > 0 ? (v / total * 100) : 0).toFixed(1)
       + '%"></i></div></td><td class="num" style="width:120px">'
       + (useKrw ? krw(v) : usd(v)) + '</td><td class="num muted" style="width:62px">'
       + pct(v, total) + '</td></tr>';
  });
  if (rest > 0) {
    h += '<tr><td class="muted">그 외 ' + (rows.length - top.length) + '개</td>'
       + '<td><div class="bar"><i style="width:' + (total > 0 ? (rest / total * 100) : 0).toFixed(1)
       + '%;background:#4a4a55"></i></div></td><td class="num muted">'
       + (useKrw ? krw(rest) : usd(rest)) + '</td><td class="num muted">' + pct(rest, total) + '</td></tr>';
  }
  return h + '</table>';
}

// "3분 전" 같은 상대 표기. 서버가 내려준 now 를 기준으로 재서 이 PC 시계가
// 틀어져 있어도 "음수 전" 같은 숫자가 안 나온다.
function agoMin(now, then) {
  if (!then) return null;
  const a = Date.parse(String(now).replace(" ", "T") + "Z");
  const b = Date.parse(String(then).replace(" ", "T") + "Z");
  if (!isFinite(a) || !isFinite(b)) return null;
  return Math.max(0, Math.round((a - b) / 60000));
}
function agoText(m) {
  if (m === null) return "기록 없음";
  if (m < 1) return "방금";
  if (m < 60) return m + "분 전";
  if (m < 1440) return Math.floor(m / 60) + "시간 전";
  return Math.floor(m / 1440) + "일 전";
}
function hs(cls, label, value, when) {
  return '<div class="hs ' + cls + '"><span class="dot"></span><b>' + esc(label) + '</b> '
       + esc(value) + (when ? ' <span class="when">' + esc(when) + '</span>' : '') + '</div>';
}

async function loadHealth() {
  const el = document.getElementById("health");
  const d = await api("/admin/health");
  if (!d.ok) { el.innerHTML = hs("bad", "상태", "확인할 수 없습니다"); return; }

  // 동기화는 1분 크론이다. 10분 넘게 소식이 없으면 멈춘 것으로 본다.
  const sy = d.sync || {};
  const sm = agoMin(d.now, sy.at);
  const syncCls = !sy.at ? "bad" : (!sy.ok ? "bad" : (sm > 10 ? "warn" : ""));
  const syncTxt = !sy.at ? "돈 적 없음" : (sy.ok ? "정상" : "실패");

  // 사용량은 밤에 0 인 게 정상이라 '없음' 을 빨갛게 하지 않는다. 시각만 보여준다.
  const um = agoMin(d.now, d.usage.last_at);

  el.innerHTML =
      hs(syncCls, "시트 동기화", syncTxt, agoText(sm) + " 확인")
    + hs("", "사용량 수집", "마지막 기록", agoText(um))
    + hs("", "오늘", nf(d.usage.today_images) + "장", "")
    + hs(d.fx ? "" : "warn", "환율", d.fx ? (nf(Math.round(d.fx.usd_krw)) + "원") : "없음",
         d.fx ? d.fx.day : "");
}

async function loadUsage() {
  markPreset(); markGb();
  loadHealth();
  const f = document.getElementById("from").value || "2000-01-01";
  const t = document.getElementById("to").value || "2999-12-31";
  const days = periodLabel(f, t);
  document.getElementById("trend").innerHTML = '<div class="empty">불러오는 중…</div>';
  const d = await api("/admin/usage?from=" + f + "&to=" + t + "&group_by=" + curGb);
  const tbl = document.getElementById("usageTable");
  if (!d.ok) {
    document.getElementById("kpis").innerHTML = "";
    document.getElementById("trend").innerHTML = "";
    tbl.innerHTML = '<div class="err" style="padding:14px 2px">' + esc(d.error || "조회 실패") + '</div>';
    return;
  }
  const rate = d.usd_krw || 0;
  // 그 날 환율로 이미 SQL 이 환산해 준다. 환율 이력이 아직 없으면(첫 실행) 최신 값으로 메운다.
  const totalKrw = d.total_cost_krw || (d.total_cost_usd * rate);
  const useKrw = totalKrw > 0;
  const rows = (d.rows || []).map(r => ({ ...r, cost_krw: r.cost_krw || (r.cost_usd * rate) }));
  const dayRows = (d.days || []).map(r => ({ ...r, cost_krw: r.cost_krw || (r.cost_usd * rate) }));

  const perDay = days > 0 ? totalKrw / days : 0;
  const busiest = dayRows.slice().sort((a, b) => (b.cost_krw || 0) - (a.cost_krw || 0))[0];
  document.getElementById("kpis").innerHTML =
      kpi("총 비용", useKrw ? krw(totalKrw) : usd(d.total_cost_usd),
          useKrw ? usd(d.total_cost_usd) : "환율을 아직 못 받았습니다")
    + kpi("이미지", nf(d.total_images) + "장",
          days > 0 ? "하루 평균 " + avg(d.total_images / days) + "장" : "")
    + kpi("하루 평균 비용", useKrw ? krw(perDay) : usd(d.total_cost_usd / Math.max(days, 1)),
          busiest ? "가장 많은 날 " + busiest.day + " · " + (useKrw ? krw(busiest.cost_krw) : usd(busiest.cost_usd)) : "")
    + (d.unpriced_images > 0
        ? kpi("단가 미설정", nf(d.unpriced_images) + "장", "단가 탭에서 채우면 이 기간 금액이 다시 계산됩니다", true)
        : kpi("단가 확인", "전부 확인됨", "이 기간의 모든 모델에 단가가 있습니다"));

  document.getElementById("trend").innerHTML = trendSvg(fillDays(f, t, dayRows), useKrw);

  if (!rows.length) {
    document.getElementById("shareChart").innerHTML = "";
    tbl.innerHTML = '<div class="empty">이 기간에 기록이 없습니다.</div>';
    return;
  }
  document.getElementById("shareChart").innerHTML = shareBars(rows, totalKrw || d.total_cost_usd, useKrw);

  let h = '<table><tr><th>' + esc(gbName(curGb)) + '</th><th class="num">장수</th>'
        + '<th class="num">비중</th>' + (useKrw ? '<th class="num">비용(원)</th>' : '')
        + '<th class="num">비용(USD)</th><th class="num">기간</th></tr>';
  for (const r of rows) {
    const v = useKrw ? r.cost_krw : r.cost_usd;
    h += '<tr><td>' + esc(r.label)
      + (r.archived ? ' <span class="pill arch">보관</span>' : '')
      + (r.unpriced > 0 ? ' <span class="pill">단가없음</span>' : '') + '</td>'
      + '<td class="num">' + nf(r.images) + '</td>'
      + '<td class="num muted">' + pct(v, useKrw ? totalKrw : d.total_cost_usd) + '</td>'
      + (useKrw ? '<td class="num">' + krw(r.cost_krw) + '</td>' : '')
      + '<td class="num muted">' + usd(r.cost_usd) + '</td>'
      + '<td class="num muted">' + esc(r.first_day === r.last_day ? r.first_day
          : (String(r.first_day).slice(5) + "~" + String(r.last_day).slice(5))) + '</td></tr>';
  }
  h += '<tfoot><tr><td>합계</td><td class="num">' + nf(d.total_images) + '</td><td class="num">100%</td>'
     + (useKrw ? '<td class="num">' + krw(totalKrw) + '</td>' : '')
     + '<td class="num">' + usd(d.total_cost_usd) + '</td><td></td></tr></tfoot></table>';
  tbl.innerHTML = h;
  document.getElementById("fxLine").textContent = d.usd_krw
    ? "1 USD = " + nf(Math.round(d.usd_krw)) + "원 (" + d.fx_day + " 기준)" : "";
}
function kpi(k, v, s, flag) {
  return '<div class="kpi' + (flag ? ' flag' : '') + '"><div class="k">' + esc(k) + '</div>'
       + '<div class="v">' + esc(v) + '</div><div class="s">' + esc(s || "") + '</div></div>';
}

/* ---- 팀 · 프로젝트 ---- */
function catTable(items, kind) {
  const live = items.filter(x => x.active), arch = items.filter(x => !x.active);
  const off = kind === "team" ? "data-off-team" : "data-off-proj";
  const on = kind === "team" ? "data-on-team" : "data-on-proj";
  const del = kind === "team" ? "data-del-team" : "data-del-proj";
  const tag = x => (x.source === "sheet") ? '' : ' <span class="pill arch">직접</span>';
  const mk = (list, btn, cls, withDel) => '<table>' + list.map(x =>
      '<tr><td>' + esc(x.name) + tag(x) + '</td><td class="muted" style="font-size:11px">' + esc(x.id) + '</td>'
    + '<td class="num"><button class="ghost" ' + btn + '="' + esc(x.id) + '" data-name="'
    + esc(x.name) + '">' + cls + '</button>'
    + (withDel ? ' <button class="ghost" ' + del + '="' + esc(x.id) + '" data-name="'
                 + esc(x.name) + '">삭제</button>' : '')
    + '</td></tr>').join("") + '</table>';
  let h = live.length ? mk(live, off, "보관", false)
        : '<div class="empty">아직 없습니다. 추가하면 앱에서 바로 보입니다.</div>';
  if (arch.length) {
    h += '<div class="sub" style="margin:14px 0 4px">보관됨 ' + arch.length + '개 '
       + '<span class="muted">&mdash; 앱 목록에는 안 보이지만 지난 비용은 리포트에 남아 있습니다</span></div>'
       + mk(arch, on, "되살리기", true);
  }
  return h;
}
async function loadSync() {
  const d = await api("/admin/sync");
  const el = document.getElementById("syncLast");
  const l = d && d.last;
  if (!l) { el.textContent = "아직 동기화한 적이 없습니다."; return; }
  const when = String(l.at || "").replace("T", " ");
  const chg = String(l.changed_at || "").replace("T", " ");
  if (!l.ok) {
    el.innerHTML = '<span class="err">마지막 시도 ' + esc(when) + ' 실패 &mdash; '
                 + esc(l.detail || "") + '</span>';
    return;
  }
  el.innerHTML = '마지막 확인 ' + esc(when) + ' &middot; ' + esc(l.detail || "")
    + (chg ? '<br><span class="muted">마지막 변경 ' + esc(chg) + '</span>' : '');
}
async function runSync() {
  const b = document.getElementById("syncBtn");
  b.disabled = true; b.textContent = "동기화 중…";
  const d = await api("/admin/sync", { method: "POST", body: "{}" });
  b.disabled = false; b.textContent = "지금 동기화";
  if (!d.ok) alert(d.error || "동기화 실패");
  loadSync(); loadCatalog();
}

async function loadCatalog() {
  const d = await api("/admin/catalog");
  document.getElementById("teamList").innerHTML = catTable(d.teams || [], "team");
  document.getElementById("projList").innerHTML = catTable(d.projects || [], "proj");
}
async function addTeam() {
  const name = document.getElementById("tName").value.trim();
  if (!name) return;
  const d = await api("/admin/team", { method: "POST", body: JSON.stringify({ name }) });
  if (!d.ok) return alert(d.error || "실패");
  document.getElementById("tName").value = "";
  loadCatalog();
}
async function addProject() {
  const name = document.getElementById("pName").value.trim();
  if (!name) return alert("이름을 입력하세요");
  const d = await api("/admin/project", { method: "POST", body: JSON.stringify({ name }) });
  if (!d.ok) return alert(d.error || "실패");
  document.getElementById("pName").value = "";
  loadCatalog();
}

/* ---- 단가 · 환율 ---- */
async function loadPrices() {
  const d = await api("/admin/prices");
  const rows = d.prices || [];
  let h = '<table><tr><th>모델</th><th>적용 시작</th><th>방식</th>'
        + '<th class="num">입력 텍스트 / 이미지 (1M)</th><th class="num">출력 (1M)</th>'
        + '<th class="num">장당</th><th class="num">장당(고화소)</th><th class="num">픽셀 기준</th>'
        + '<th class="num">레퍼런스 장당 / 무료</th><th>상태</th><th></th></tr>';
  rows.forEach((p, i) => {
    const id = "p_" + i;
    const latest = !rows.some(q => q.model === p.model && q.effective_from > p.effective_from);
    h += '<tr><td>' + esc(p.model) + '<div class="muted" style="font-size:11px">' + esc(p.note || "") + '</div></td>'
      + '<td class="muted">' + esc(p.effective_from)
      + (latest ? ' <span class="ok" style="font-size:11px">현재</span>' : '') + '</td>'
      + '<td><select id="' + id + '_mode"><option value="token"' + (p.mode === "token" ? " selected" : "") + '>토큰</option>'
      + '<option value="image"' + (p.mode === "image" ? " selected" : "") + '>장수</option></select></td>'
      + '<td class="num"><input id="' + id + '_it" value="' + p.in_text_per_m + '" size="4"> / '
      + '<input id="' + id + '_ii" value="' + p.in_image_per_m + '" size="4"></td>'
      + '<td class="num"><input id="' + id + '_o" value="' + p.out_per_m + '" size="5"></td>'
      + '<td class="num"><input id="' + id + '_pi" value="' + p.per_image + '" size="5"></td>'
      + '<td class="num"><input id="' + id + '_ph" value="' + p.per_image_hi + '" size="5"></td>'
      + '<td class="num"><input id="' + id + '_px" value="' + p.px_threshold + '" size="8"></td>'
      + '<td class="num"><input id="' + id + '_ip" value="' + p.in_per_image + '" size="5"> / '
      + '<input id="' + id + '_if" value="' + p.in_free_count + '" size="2"></td>'
      + '<td>' + (p.verified ? '<span class="ok">확인됨</span>' : '<span class="pill">확인 필요</span>') + '</td>'
      + '<td class="num"><button class="ghost" data-save-price="' + esc(p.model) + '" data-pid="' + id
      + '" data-eff="' + esc(p.effective_from) + '">이 행 수정</button>'
      + (latest ? ' <button class="ghost" data-save-price="' + esc(p.model) + '" data-pid="' + id
                  + '" data-eff="today">오늘부터 새 단가</button>' : '')
      + '</td></tr>';
  });
  document.getElementById("priceTable").innerHTML = h + '</table>';
}
// "이 행 수정" 은 오타 정정, "오늘부터 새 단가" 는 시점을 새로 여는 것이다.
// 단가가 바뀐 걸 기존 행에 덮어쓰면 지난달 집계까지 새 단가로 다시 계산된다.
async function savePrice(model, id, eff) {
  const v = k => document.getElementById(id + k).value;
  const when = eff === "today" ? new Date().toISOString().slice(0, 10) : eff;
  if (eff === "today" && !confirm(when + " 부터 적용되는 새 단가로 넣습니다. 그 전 기간은 지금 단가 그대로 남습니다.")) return;
  const d = await api("/admin/prices", { method: "POST", body: JSON.stringify({
    model, effective_from: when, mode: v("_mode"),
    in_text_per_m: v("_it"), in_image_per_m: v("_ii"), out_per_m: v("_o"),
    per_image: v("_pi"), per_image_hi: v("_ph"), px_threshold: v("_px"),
    in_per_image: v("_ip"), in_free_count: v("_if"), verified: 1 }) });
  if (!d.ok) return alert(d.error || "실패");
  loadPrices();
}
async function loadFx() {
  const d = await api("/admin/fx");
  document.getElementById("fxNow").textContent = (d.ok && d.usd_krw)
    ? ("1 USD = " + nf(Math.round(d.usd_krw)) + "원  (" + d.day + " 기준)")
    : "아직 받아온 환율이 없습니다";
}
async function refreshFx() { await api("/admin/fx?refresh=1"); loadFx(); loadUsage(); }

applyPreset("thisMonth");
</script></body></html>`;

/**
 * USD→KRW 환율. 하루 한 번 크론이 채우고, 리포트는 이벤트 '그 날' 환율로 환산한다.
 *
 * 왜 날짜별로 쌓는가: 오늘 환율로 과거 전체를 환산하면 지난달 리포트 숫자가
 * 매일 조금씩 달라진다. 확정된 숫자가 흔들리면 아무도 그 리포트를 못 믿는다.
 * 무료 소스(ECB 기반, 키 불필요)라 비용도 들지 않는다.
 */
async function fetchFxRate(env) {
  try {
    const r = await fetch("https://api.frankfurter.app/latest?from=USD&to=KRW",
                          { headers: { "User-Agent": "NanoBanana/1.0" } });
    if (!r.ok) return { ok: false, error: "HTTP " + r.status };
    const d = await r.json();
    const rate = d && d.rates && d.rates.KRW;
    if (!rate) return { ok: false, error: "no rate" };
    const day = (d.date || new Date().toISOString().slice(0, 10)).slice(0, 10);
    await env.USAGE_DB.prepare(
      `INSERT INTO fx_rates (day, usd_krw, source, created_at) VALUES (?,?,?,?)
       ON CONFLICT(day) DO UPDATE SET usd_krw=excluded.usd_krw`)
      .bind(day, Number(rate), "frankfurter", nowIso()).run();
    return { ok: true, day, usd_krw: Number(rate) };
  } catch (e) {
    return { ok: false, error: String(e).slice(0, 80) };
  }
}

/** 가장 최근에 알고 있는 환율. 한 번도 못 받았으면 null. */
async function latestFx(env) {
  const r = await env.USAGE_DB.prepare(
    "SELECT day, usd_krw FROM fx_rates ORDER BY day DESC LIMIT 1").first();
  return r || null;
}


/**
 * 목록에서 완전히 지운다 — 오타로 만든 이름을 치우는 용도.
 *
 * 한 장이라도 비용이 잡힌 적이 있으면 거부한다. 지워버리면 그 행의 이름표가
 * 사라져 리포트에 슬러그만 남고, 무엇보다 "끝난 프로젝트의 지난 비용" 이라는
 * 이 집계의 존재 이유가 무너진다. 그런 건 지우는 게 아니라 **보관**하는 것이다.
 */
async function dropCatalogRow(env, table, col, id) {
  const used = await env.USAGE_DB.prepare(
    `SELECT COUNT(*) AS n FROM usage_events WHERE ${col} = ?`).bind(id).first();
  if (used && used.n > 0) {
    return { ok: false, used: used.n,
             error: "사용 기록이 " + used.n + "건 있어 지울 수 없습니다. 보관으로 내려두세요." };
  }
  await env.USAGE_DB.prepare(`DELETE FROM ${table} WHERE id = ?`).bind(id).run();
  return { ok: true, deleted: id };
}

/**
 * 사람이 읽는 순서로 정렬한다.
 *
 * SQL 의 `ORDER BY name` 은 문자열 비교라 "10팀" 이 "1팀" 보다 앞에 온다. 숫자가
 * 섞인 이름을 고르는 화면에서 그건 그냥 고장난 목록으로 보인다. 숫자 덩어리는
 * 숫자로, 나머지는 코드포인트로 비교한다 — 현대 한글 음절은 코드포인트 순서가
 * 가나다 순서와 같으므로 별도 로케일 데이터 없이도 맞다.
 */
function natCmp(a, b) {
  const re = /(\d+)|(\D+)/g;
  const ax = String(a || "").match(re) || [], bx = String(b || "").match(re) || [];
  for (let i = 0; i < Math.min(ax.length, bx.length); i++) {
    const an = /^\d/.test(ax[i]), bn = /^\d/.test(bx[i]);
    if (an && bn) {
      const d = Number(ax[i]) - Number(bx[i]);
      if (d) return d;
    } else if (ax[i] !== bx[i]) {
      return ax[i] < bx[i] ? -1 : 1;
    }
  }
  return ax.length - bx.length;
}
const byName = (x, y) => natCmp(x.name || x.id, y.name || y.id);

/**
 * 앱이 실행할 때 받아가는 목록. 서버에서 바꾸면 다음 실행에 바로 반영된다.
 * 앱에는 살아 있는 것만 주고, 관리자 화면은 보관된 것까지 받아 되살릴 수 있다.
 */
async function handleCatalog(env, includeArchived) {
  const w = includeArchived ? "" : " WHERE active=1";
  const teams = await env.USAGE_DB.prepare(
    "SELECT id, name, active, source FROM teams" + w).all();
  const projects = await env.USAGE_DB.prepare(
    "SELECT id, name, team_id, active, source FROM projects" + w).all();
  return {
    ok: true,
    teams: (teams.results || []).sort(byName),
    projects: (projects.results || []).sort(byName),
  };
}

/**
 * 앱이 모아서 보내는 사용량 배치.
 *
 * id 는 앱이 만든다. 재전송(네트워크 실패 후 재시도)해도 INSERT OR IGNORE 가
 * 같은 행을 두 번 쌓지 않게 막아준다 — 앱 쪽 spool 은 "보냈는지" 를 확신할 수
 * 없으므로 중복 전송이 정상 동작이고, 그걸 서버가 흡수해야 한다.
 */
async function handleUsage(request, env, rec) {
  let body;
  try { body = await request.json(); } catch { return { st: 400, b: { ok: false, error: "bad request" } }; }
  const events = Array.isArray(body.events) ? body.events.slice(0, 500) : [];
  if (!events.length) return { st: 200, b: { ok: true, accepted: 0 } };

  // out_px / ref_images 는 앱이 처음부터 보내던 값인데 이 칸이 없어 버려지고
  // 있었다 — 그래서 픽셀 구간제와 레퍼런스 과금이 한 번도 적용되지 않았다.
  const stmt = env.USAGE_DB.prepare(
    `INSERT OR IGNORE INTO usage_events
       (id, ts, day, team_id, project_id, token_id, user, machine,
        provider, model, size, quality, images,
        in_text_tokens, in_image_tokens, out_tokens, out_px, ref_images,
        elapsed_ms, app_version, created_at)
     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  const created = nowIso();
  const batch = [];
  for (const e of events) {
    const ts = String(e.ts || created).slice(0, 19);
    if (!e.id || !e.team_id || !e.project_id || !e.model) continue;
    batch.push(stmt.bind(
      String(e.id).slice(0, 64), ts, ts.slice(0, 10),
      String(e.team_id).slice(0, 48), String(e.project_id).slice(0, 48),
      rec.token_id || null, rec.user || null, rec.machine || null,
      String(e.provider || "").slice(0, 24), String(e.model).slice(0, 64),
      String(e.size || "").slice(0, 24), String(e.quality || "").slice(0, 16),
      Number(e.images) || 1,
      Number(e.in_text_tokens) || 0, Number(e.in_image_tokens) || 0,
      Number(e.out_tokens) || 0, Number(e.out_px) || 0, Number(e.ref_images) || 0,
      e.elapsed_ms == null ? null : Number(e.elapsed_ms),
      String(e.app_version || "").slice(0, 32), created));
  }
  if (!batch.length) return { st: 200, b: { ok: true, accepted: 0 } };
  await env.USAGE_DB.batch(batch);

  // 새 모델이 조용히 $0 으로 집계되는 걸 막는다. 가격을 자동으로 알아낼 방법은
  // 없지만(세 프로바이더 모두 가격 API 가 없다), 빠졌다는 사실은 자동으로
  // 드러나게 할 수 있다 — 관리자 페이지가 이 행들을 '확인 필요' 로 띄운다.
  const seen = [...new Set(events.map((e) => String(e.model || "")).filter(Boolean))];
  for (const m of seen.slice(0, 20)) {
    try {
      const hit = await env.USAGE_DB.prepare(
        "SELECT model FROM prices WHERE model=? LIMIT 1").bind(m).first();
      if (!hit) {
        await env.USAGE_DB.prepare(
          `INSERT OR IGNORE INTO prices
             (model, effective_from, provider, mode, verified, note, updated_at)
           VALUES (?,?,?,?,0,?,?)`)
          .bind(m, "2000-01-01",
                String((events.find((e) => e.model === m) || {}).provider || ""),
                "token", "새 모델 - 단가 미설정", nowIso()).run();
      }
    } catch {}
  }
  return { st: 200, b: { ok: true, accepted: batch.length } };
}

/** 단가표를 붙여 금액까지 계산한 집계. group_by 는 화이트리스트로만 받는다. */
const GROUPS = {
  team: "e.team_id", project: "e.project_id", user: "e.user",
  model: "e.model", day: "e.day", machine: "e.machine",
};

/**
 * 한 행(=이미지 한 장)의 비용식. 집계 쿼리와 일별 쿼리가 **같은 문자열**을 쓴다 —
 * 두 벌로 적어두면 언젠가 한쪽만 고쳐져 합계와 그래프가 어긋난다.
 *
 * mode='image' 는 장당가인데 출력 픽셀이 px_threshold 를 넘으면 고화소 단가를 쓰고
 * (seedream-5-0-pro: 2.61M px 초과 $0.09), 레퍼런스는 무료 장수를 뺀 만큼만 붙는다.
 */
const COST_USD = `
  COALESCE(
    CASE WHEN p.mode='image'
         THEN e.images * (CASE WHEN p.px_threshold > 0 AND e.out_px > p.px_threshold
                               THEN p.per_image_hi ELSE p.per_image END)
            + MAX(e.ref_images - p.in_free_count, 0) * p.in_per_image
         ELSE e.in_text_tokens  * p.in_text_per_m  / 1000000.0
            + e.in_image_tokens * p.in_image_per_m / 1000000.0
            + e.out_tokens      * p.out_per_m      / 1000000.0
    END, 0)`;

/**
 * 그 이미지가 만들어진 **날짜에 유효했던** 단가 한 행만 고른다.
 *
 * 단순히 model 로만 조인하면 단가를 한 번이라도 바꾼 모델은 행이 두 벌로 매칭돼
 * 비용이 그대로 두 배가 된다. prices 의 PK 가 (model, effective_from) 이므로
 * 그 날짜 이하 중 가장 최근 것 하나로 못박아야 한다.
 */
const PRICE_JOIN = `
  LEFT JOIN prices p
    ON p.model = e.model
   AND p.effective_from = (SELECT MAX(p2.effective_from) FROM prices p2
                            WHERE p2.model = e.model AND p2.effective_from <= e.day)`;

/**
 * 환산은 **그 날의 환율**로 한다. 오늘 환율로 과거 전체를 환산하면 지난달 리포트가
 * 매일 조금씩 달라져서 아무도 그 숫자를 못 믿는다. 그 날 환율이 없으면(주말·공휴일)
 * 직전 영업일 값을 쓰고, 아예 하나도 없으면 화면이 최신 환율로 대체한다.
 */
const FX_JOIN = `
  LEFT JOIN fx_rates f
    ON f.day = (SELECT MAX(f2.day) FROM fx_rates f2 WHERE f2.day <= e.day)`;

// 환율 수집을 시작하기 전에 만든 이미지는 그 날 환율이 아예 없다. 0 으로 두면
// 원화 합계가 조용히 적게 나온다 — 가진 것 중 가장 오래된 환율로 메운다.
// (오늘 환율로 메우면 지난달 숫자가 매일 달라진다.)
const KRW_RATE = `COALESCE(f.usd_krw, (SELECT usd_krw FROM fx_rates ORDER BY day LIMIT 1), 0)`;

async function handleAdminUsage(url, env) {
  const from = (url.searchParams.get("from") || "0000-01-01").slice(0, 10);
  const to = (url.searchParams.get("to") || "9999-12-31").slice(0, 10);
  const key = url.searchParams.get("group_by") || "project";
  const col = GROUPS[key];
  if (!col) return { ok: false, error: "bad group_by" };

  const sql = `
    SELECT ${col} AS k,
           COUNT(*)            AS rows_n,
           SUM(e.images)       AS images,
           SUM(e.out_tokens)   AS out_tokens,
           SUM(${COST_USD})    AS cost_usd,
           SUM(${COST_USD} * ${KRW_RATE}) AS cost_krw,
           SUM(CASE WHEN p.model IS NULL OR p.verified=0 THEN e.images ELSE 0 END) AS unpriced,
           MIN(e.day) AS first_day, MAX(e.day) AS last_day
    FROM usage_events e
    ${PRICE_JOIN}
    ${FX_JOIN}
    WHERE e.day >= ? AND e.day <= ?
    GROUP BY k
    ORDER BY cost_usd DESC`;
  const r = await env.USAGE_DB.prepare(sql).bind(from, to).all();
  const rows = r.results || [];

  // 그래프용 일별 추이. 같은 비용식을 쓰므로 막대 합계와 카드 합계가 어긋나지 않는다.
  const dsql = `
    SELECT e.day AS day, SUM(e.images) AS images,
           SUM(${COST_USD}) AS cost_usd,
           SUM(${COST_USD} * ${KRW_RATE}) AS cost_krw
    FROM usage_events e
    ${PRICE_JOIN}
    ${FX_JOIN}
    WHERE e.day >= ? AND e.day <= ?
    GROUP BY e.day ORDER BY e.day`;
  const dr = await env.USAGE_DB.prepare(dsql).bind(from, to).all();

  const total = rows.reduce((a, x) => a + (x.cost_usd || 0), 0);
  const totalKrw = rows.reduce((a, x) => a + (x.cost_krw || 0), 0);
  const imgs = rows.reduce((a, x) => a + (x.images || 0), 0);
  const unpriced = rows.reduce((a, x) => a + (x.unpriced || 0), 0);

  // 이름과 보관 여부를 붙여 돌려준다. **active 로 거르지 않는다** — 끝난 프로젝트를
  // 목록에서 내렸다고 그 프로젝트가 쓴 돈까지 안 보이면 집계가 아니라 구멍이다.
  let names = {}, archived = {};
  if (key === "team" || key === "project") {
    const t = await env.USAGE_DB.prepare(
      `SELECT id, name, active FROM ${key === "team" ? "teams" : "projects"}`).all();
    for (const x of (t.results || [])) { names[x.id] = x.name; archived[x.id] = !x.active; }
  }
  const fx = await latestFx(env);
  return {
    ok: true, group_by: key, from, to,
    total_cost_usd: total, total_cost_krw: totalKrw,
    total_images: imgs, unpriced_images: unpriced,
    usd_krw: fx ? fx.usd_krw : 0, fx_day: fx ? fx.day : null,
    days: dr.results || [],
    rows: rows.map((x) => ({
      ...x, label: names[x.k] || x.k, archived: Boolean(archived[x.k]),
    })),
  };
}

export default {
  // 하루 한 번 환율을 받아 둔다. 놓친 날은 리포트가 직전 값으로 대체한다.
  /**
   * 1분마다 도는 크론 하나로 두 가지를 한다 (무료 플랜 크론 한도가 계정당 5개고
   * 이미 다 써서 두 번째를 못 붙인다).
   *
   * 시트 동기화: 매분. 일과 중에 프로젝트를 '종료' 로 바꾸면 1분 안에 앱 목록에서
   *   내려가야 한다. 내용이 그대로면 D1 에 아무것도 쓰지 않는다.
   * 환율: 오늘 치가 없을 때만. 시각을 못 박으면 그 1분을 놓친 날은 통째로 빈다 —
   *   "없으면 받는다" 로 두면 저절로 따라잡는다.
   */
  async scheduled(event, env, ctx) {
    ctx.waitUntil(syncSheetsSafe(env));
    ctx.waitUntil((async () => {
      try {
        const cur = await latestFx(env);
        const today = new Date().toISOString().slice(0, 10);
        if (!cur || cur.day < today) await fetchFxRate(env);
      } catch {}
    })());
  },

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
    // 앱이 실행 때 받아가는 팀/프로젝트 목록 — 토큰이 있어야 한다.
    if (path === "/catalog" && request.method === "GET") {
      const g = await tokenRecord(request, env);
      if (!g.ok) return json({ ok: false, error: g.error }, g.status);
      return json(await handleCatalog(env));
    }
    // 앱이 모아 보내는 사용량 배치.
    if (path === "/usage" && request.method === "POST") {
      const g = await tokenRecord(request, env);
      if (!g.ok) return json({ ok: false, error: g.error }, g.status);
      const r = await handleUsage(request, env, g.rec);
      return json(r.b, r.st);
    }
    // 페이지 자체는 키 없이 내려준다 — 안에서 키를 물어보고, 데이터 요청마다
    // 헤더로 검사한다. 주소창으로는 헤더를 붙일 수 없기 때문.
    if (path === "/admin" || path === "/admin/") {
      return new Response(ADMIN_HTML, {
        headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
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
