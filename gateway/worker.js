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
    return json(await handleCatalog(env));
  }
  // 팀·프로젝트 추가/수정. active=0 으로 두면 앱 목록에서 사라지지만 과거
  // 사용량 행은 그대로 남는다 — 지난달 집계가 조용히 바뀌면 안 되기 때문.
  if (path === "/admin/team" && request.method === "POST") {
    let b; try { b = await request.json(); } catch { return json({ ok: false, error: "bad request" }, 400); }
    const id = slug(b.id || b.name);
    if (!id) return json({ ok: false, error: "name required" }, 400);
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
      await env.USAGE_DB.prepare(
        `UPDATE prices SET mode=?, in_text_per_m=?, in_image_per_m=?, out_per_m=?,
                           per_image=?, verified=?, note=?, updated_at=?
         WHERE model=?`)
        .bind(String(b.mode || "token"), Number(b.in_text_per_m) || 0, Number(b.in_image_per_m) || 0,
              Number(b.out_per_m) || 0, Number(b.per_image) || 0, b.verified ? 1 : 0,
              String(b.note || "").slice(0, 200), nowIso(), String(b.model)).run();
      return json({ ok: true });
    }
    const r = await env.USAGE_DB.prepare(
      "SELECT * FROM prices ORDER BY provider, model").all();
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

// 관리자 페이지. 워커가 직접 서빙하므로 호스팅 비용이 없다.
// 로그인은 관리자 키 하나 — 계정 시스템을 붙일 이유가 없는 1인용 화면이다.
const ADMIN_HTML = `<!DOCTYPE html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>NanoBanana 사용량</title><link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%231e1e23'/%3E%3Crect x='7' y='17' width='4.5' height='9' rx='1.5' fill='%23D4A574'/%3E%3Crect x='13.8' y='12' width='4.5' height='14' rx='1.5' fill='%23D4A574'/%3E%3Crect x='20.6' y='6' width='4.5' height='20' rx='1.5' fill='%23E8C9A0'/%3E%3C/svg%3E"><style>
:root{--bg:#16161a;--surf:#1e1e23;--line:#2e2e36;--tx:#e9e9ee;--tx2:#9a9aa4;--acc:#D4A574;--warn:#FFD60A}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--tx);font:13px/1.55 'Malgun Gothic','Segoe UI',system-ui,sans-serif;padding:20px}
h1{font-size:17px;margin-bottom:4px}.sub{color:var(--tx2);font-size:12px;margin-bottom:16px}
.tabs{display:flex;gap:6px;margin-bottom:14px;border-bottom:1px solid var(--line)}
.tabs button{background:none;border:none;color:var(--tx2);padding:8px 14px;cursor:pointer;font:inherit;border-bottom:2px solid transparent}
.tabs button.on{color:var(--tx);border-bottom-color:var(--acc)}
.card{background:var(--surf);border:1px solid var(--line);border-radius:10px;padding:14px;margin-bottom:14px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
label{color:var(--tx2);font-size:12px}
input,select{background:#121216;color:var(--tx);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font:inherit}
button.go{background:var(--acc);color:#221a10;border:none;border-radius:6px;padding:7px 14px;font-weight:700;cursor:pointer}
button.ghost{background:#26262c;color:var(--tx);border:1px solid var(--line);border-radius:6px;padding:6px 12px;cursor:pointer}
table{width:100%;border-collapse:collapse;margin-top:10px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);font-size:12px}
th{color:var(--tx2);font-weight:600}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.big{font-size:24px;font-weight:700}
.pill{display:inline-block;background:#3a2f12;color:var(--warn);border-radius:10px;padding:2px 9px;font-size:11px}
.muted{color:var(--tx2)}.err{color:#ff8a8a}.ok{color:#7ee08a}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
</style></head><body>
<h1>NanoBanana 사용량</h1>
<div class="sub">이미지 한 장마다 팀·프로젝트로 비용이 잡힙니다. 금액은 저장하지 않고, 조회할 때 단가를 곱합니다.</div>

<div class="tabs">
  <button id="tb-usage" class="on" onclick="show('usage')">사용량</button>
  <button id="tb-setup" onclick="show('setup')">팀 · 프로젝트</button>
  <button id="tb-price" onclick="show('price')">단가 · 환율</button>
</div>

<div id="pane-usage">
  <div class="card row">
    <label>기간</label><input type="date" id="from"><span class="muted">~</span><input type="date" id="to">
    <label>묶기</label>
    <select id="gb"><option value="project">프로젝트</option><option value="team">팀</option>
      <option value="user">사람</option><option value="model">모델</option><option value="day">날짜</option>
      <option value="machine">PC</option></select>
    <button class="go" onclick="loadUsage()">조회</button>
  </div>
  <div class="card grid3" id="summary"></div>
  <div class="card"><div id="usageTable" class="muted">조회를 눌러주세요.</div></div>
</div>

<div id="pane-setup" style="display:none">
  <div class="card">
    <b>팀 추가</b>
    <div class="row" style="margin-top:8px">
      <input id="tName" placeholder="예: 디자인팀" style="min-width:200px">
      <button class="go" onclick="addTeam()">추가</button>
    </div>
    <div id="teamList"></div>
  </div>
  <div class="card">
    <b>프로젝트 추가</b>
    <div class="row" style="margin-top:8px">
      <input id="pName" placeholder="예: [26P50]DL E&amp;C PT" style="min-width:260px">
      <button class="go" onclick="addProject()">추가</button>
    </div>
    <div id="projList"></div>
  </div>
  <div class="sub">팀과 프로젝트는 각각 따로 고릅니다 &mdash; 한 건을 여러 팀이 같이 할 수 있기 때문입니다.<br>끄기를 누르면 앱 목록에서 사라집니다. 지난 사용 기록은 그대로 남습니다.</div>
</div>

<div id="pane-price" style="display:none">
  <div class="card"><b>환율</b> <span id="fxNow" class="muted"></span>
    <button class="ghost" style="margin-left:8px" onclick="refreshFx()">지금 갱신</button>
    <div class="sub" style="margin:8px 0 0">매일 자동으로 받아옵니다. 리포트는 각 이미지가 만들어진 날의 환율로 환산합니다.</div>
  </div>
  <div class="card"><b>모델 단가</b><div id="priceTable"></div>
    <div class="sub" style="margin-top:8px">단가를 바꾸면 <b>과거 집계까지 다시 계산</b>됩니다. 금액이 아니라 사용량을 저장하기 때문입니다.</div>
  </div>
</div>

<script>
const KEY_LS = "nb_admin_key";
let KEY = localStorage.getItem(KEY_LS) || "";
if (!KEY) { KEY = prompt("관리자 키") || ""; if (KEY) localStorage.setItem(KEY_LS, KEY); }
const H = () => ({ "X-Admin-Key": KEY, "Content-Type": "application/json" });
const fmtUsd = n => "$" + (Number(n) || 0).toFixed(4);
const fmtKrw = n => Math.round(Number(n) || 0).toLocaleString("ko-KR") + "원";
const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

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
  if (which === "setup") loadCatalog();
  if (which === "price") { loadPrices(); loadFx(); }
}

async function loadUsage() {
  const f = document.getElementById("from").value || "2000-01-01";
  const t = document.getElementById("to").value || "2999-12-31";
  const gb = document.getElementById("gb").value;
  const d = await api("/admin/usage?from=" + f + "&to=" + t + "&group_by=" + gb);
  const tbl = document.getElementById("usageTable");
  if (!d.ok) { tbl.innerHTML = '<span class="err">' + esc(d.error || "조회 실패") + '</span>'; return; }
  const rate = d.usd_krw || 0;
  document.getElementById("summary").innerHTML =
    '<div><div class="muted">총 비용</div><div class="big">' + fmtUsd(d.total_cost_usd) + '</div>'
    + (rate ? '<div class="muted">' + fmtKrw(d.total_cost_usd * rate) + '</div>' : '') + '</div>'
    + '<div><div class="muted">이미지</div><div class="big">' + (d.total_images || 0).toLocaleString() + '장</div></div>'
    + '<div><div class="muted">단가 미설정</div><div class="big">' + (d.unpriced_images || 0) + '장</div>'
    + (d.unpriced_images > 0 ? '<span class="pill">단가 탭에서 채워주세요</span>' : '') + '</div>';
  if (!d.rows.length) { tbl.innerHTML = '<span class="muted">이 기간에 기록이 없습니다.</span>'; return; }
  let h = '<table><tr><th>' + esc(gb) + '</th><th class="num">이미지</th><th class="num">출력 토큰</th>'
        + '<th class="num">비용(USD)</th>' + (rate ? '<th class="num">원화</th>' : '') + '</tr>';
  for (const r of d.rows) {
    h += '<tr><td>' + esc(r.label) + (r.unpriced > 0 ? ' <span class="pill">단가없음</span>' : '') + '</td>'
      + '<td class="num">' + (r.images || 0).toLocaleString() + '</td>'
      + '<td class="num">' + (r.out_tokens || 0).toLocaleString() + '</td>'
      + '<td class="num">' + fmtUsd(r.cost_usd) + '</td>'
      + (rate ? '<td class="num">' + fmtKrw((r.cost_usd || 0) * rate) + '</td>' : '') + '</tr>';
  }
  tbl.innerHTML = h + '</table>';
}

async function loadCatalog() {
  const d = await api("/admin/catalog");
  const teams = d.teams || [], projects = d.projects || [];
  document.getElementById("teamList").innerHTML = teams.length
    ? '<table><tr><th>팀</th><th>ID</th><th></th></tr>' + teams.map(t =>
        '<tr><td>' + esc(t.name) + '</td><td class="muted">' + esc(t.id) + '</td>'
        + '<td><button class="ghost" data-off-team="' + esc(t.id) + '" data-name="' + esc(t.name) + '">끄기</button></td></tr>'
      ).join("") + '</table>'
    : '<div class="muted" style="margin-top:8px">아직 팀이 없습니다. 하나 추가하면 앱에서 바로 보입니다.</div>';
  document.getElementById("projList").innerHTML = projects.length
    ? '<table><tr><th>프로젝트</th><th>ID</th><th></th></tr>' + projects.map(p =>
        '<tr><td>' + esc(p.name) + '</td>'
        + '<td class="muted">' + esc(p.id) + '</td>'
        + '<td><button class="ghost" data-off-proj="' + esc(p.id) + '" data-name="' + esc(p.name)
        + '">끄기</button></td></tr>'
      ).join("") + '</table>'
    : '<div class="muted" style="margin-top:8px">아직 프로젝트가 없습니다.</div>';
}

// 버튼 핸들러는 위임으로 붙인다 — 이름에 따옴표가 들어가도 깨지지 않는다.
document.addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.offTeam) {
    await api("/admin/team", { method: "POST", body: JSON.stringify(
      { id: b.dataset.offTeam, name: b.dataset.name, active: false }) });
    loadCatalog();
  } else if (b.dataset.offProj) {
    await api("/admin/project", { method: "POST", body: JSON.stringify(
      { id: b.dataset.offProj, name: b.dataset.name, active: false }) });
    loadCatalog();
  } else if (b.dataset.savePrice) {
    savePrice(b.dataset.savePrice, b.dataset.pid);
  }
});

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

async function loadPrices() {
  const d = await api("/admin/prices");
  const rows = d.prices || [];
  let h = '<table><tr><th>모델</th><th>방식</th><th class="num">입력 텍스트/이미지 (1M)</th>'
        + '<th class="num">출력 (1M)</th><th class="num">장당</th><th class="num">장당(고화소)</th>'
        + '<th class="num">픽셀 기준</th><th>상태</th><th></th></tr>';
  for (const p of rows) {
    const id = "p_" + p.model.replace(/[^a-z0-9]/gi, "_");
    h += '<tr><td>' + esc(p.model) + '<div class="muted" style="font-size:11px">' + esc(p.note || "") + '</div></td>'
      + '<td><select id="' + id + '_mode"><option value="token"' + (p.mode === "token" ? " selected" : "") + '>토큰</option>'
      + '<option value="image"' + (p.mode === "image" ? " selected" : "") + '>장수</option></select></td>'
      + '<td class="num"><input id="' + id + '_it" value="' + p.in_text_per_m + '" size="5"> / '
      + '<input id="' + id + '_ii" value="' + p.in_image_per_m + '" size="5"></td>'
      + '<td class="num"><input id="' + id + '_o" value="' + p.out_per_m + '" size="6"></td>'
      + '<td class="num"><input id="' + id + '_pi" value="' + p.per_image + '" size="6"></td>'
      + '<td class="num"><input id="' + id + '_ph" value="' + p.per_image_hi + '" size="6"></td>'
      + '<td class="num"><input id="' + id + '_px" value="' + p.px_threshold + '" size="8"></td>'
      + '<td>' + (p.verified ? '<span class="ok">확인됨</span>' : '<span class="pill">확인 필요</span>') + '</td>'
      + '<td><button class="ghost" data-save-price="' + esc(p.model) + '" data-pid="' + id + '">저장</button></td></tr>';
  }
  document.getElementById("priceTable").innerHTML = h + '</table>';
}
async function savePrice(model, id) {
  const v = k => document.getElementById(id + k).value;
  const d = await api("/admin/prices", { method: "POST", body: JSON.stringify({
    model, mode: v("_mode"), in_text_per_m: v("_it"), in_image_per_m: v("_ii"),
    out_per_m: v("_o"), per_image: v("_pi"), per_image_hi: v("_ph"),
    px_threshold: v("_px"), verified: 1 }) });
  if (!d.ok) return alert(d.error || "실패");
  loadPrices();
}
async function loadFx() {
  const d = await api("/admin/fx");
  document.getElementById("fxNow").textContent = (d.ok && d.usd_krw)
    ? ("1 USD = " + Number(d.usd_krw).toLocaleString("ko-KR") + "원  (" + d.day + " 기준)")
    : "아직 받아온 환율이 없습니다";
}
async function refreshFx() { await api("/admin/fx?refresh=1"); loadFx(); }

const today = new Date().toISOString().slice(0, 10);
document.getElementById("from").value = today.slice(0, 8) + "01";
document.getElementById("to").value = today;
loadUsage();
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


/** 앱이 실행할 때 받아가는 목록. 서버에서 바꾸면 다음 실행에 바로 반영된다. */
async function handleCatalog(env) {
  const teams = await env.USAGE_DB.prepare(
    "SELECT id, name FROM teams WHERE active=1 ORDER BY name").all();
  const projects = await env.USAGE_DB.prepare(
    "SELECT id, name, team_id FROM projects WHERE active=1 ORDER BY name").all();
  return { ok: true, teams: teams.results || [], projects: projects.results || [] };
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

  const stmt = env.USAGE_DB.prepare(
    `INSERT OR IGNORE INTO usage_events
       (id, ts, day, team_id, project_id, token_id, user, machine,
        provider, model, size, quality, images,
        in_text_tokens, in_image_tokens, out_tokens, elapsed_ms, app_version, created_at)
     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
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
      Number(e.out_tokens) || 0,
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

async function handleAdminUsage(url, env) {
  const from = (url.searchParams.get("from") || "0000-01-01").slice(0, 10);
  const to = (url.searchParams.get("to") || "9999-12-31").slice(0, 10);
  const key = url.searchParams.get("group_by") || "project";
  const col = GROUPS[key];
  if (!col) return { ok: false, error: "bad group_by" };

  // 비용식은 SQL 안에서 단가표와 조인해 만든다. mode='image' 면 장수 × 장당가,
  // 아니면 토큰 × (단가/1M).
  const sql = `
    SELECT ${col} AS k,
           COUNT(*)            AS rows_n,
           SUM(e.images)       AS images,
           SUM(e.out_tokens)   AS out_tokens,
           SUM(COALESCE(
             CASE WHEN p.mode='image'
                  THEN e.images * p.per_image
                  ELSE e.in_text_tokens  * p.in_text_per_m  / 1000000.0
                     + e.in_image_tokens * p.in_image_per_m / 1000000.0
                     + e.out_tokens      * p.out_per_m      / 1000000.0
             END, 0))          AS cost_usd,
           SUM(CASE WHEN p.model IS NULL OR p.verified=0 THEN e.images ELSE 0 END) AS unpriced
    FROM usage_events e
    LEFT JOIN prices p ON p.model = e.model
    WHERE e.day >= ? AND e.day <= ?
    GROUP BY k
    ORDER BY cost_usd DESC`;
  const r = await env.USAGE_DB.prepare(sql).bind(from, to).all();
  const rows = r.results || [];
  const total = rows.reduce((a, x) => a + (x.cost_usd || 0), 0);
  const imgs = rows.reduce((a, x) => a + (x.images || 0), 0);
  const unpriced = rows.reduce((a, x) => a + (x.unpriced || 0), 0);
  // 이름을 붙여 돌려준다 — 화면에서 다시 조회하지 않아도 되게.
  let names = {};
  if (key === "team" || key === "project") {
    const t = await env.USAGE_DB.prepare(
      `SELECT id, name FROM ${key === "team" ? "teams" : "projects"}`).all();
    for (const x of (t.results || [])) names[x.id] = x.name;
  }
  const fx = await latestFx(env);
  return {
    ok: true, group_by: key, from, to,
    total_cost_usd: total, total_images: imgs, unpriced_images: unpriced,
    usd_krw: fx ? fx.usd_krw : 0, fx_day: fx ? fx.day : null,
    rows: rows.map((x) => ({ ...x, label: names[x.k] || x.k })),
  };
}

export default {
  // 하루 한 번 환율을 받아 둔다. 놓친 날은 리포트가 직전 값으로 대체한다.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(fetchFxRate(env));
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
