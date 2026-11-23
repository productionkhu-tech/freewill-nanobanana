// Task 9: the gateway's POST /personal/check (spec 5.4). The personal edition sends the first 8 hex of a
// key's sha256; the worker answers with the full hashes of company keys starting with them.
// usage: node tests/personal/t_worker_check.mjs
import { createHash } from "node:crypto";
import { copyFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

// worker.js is an ES module without a package.json saying so: load a .mjs copy (it imports nothing).
const here = dirname(fileURLToPath(import.meta.url));
const copy = join(tmpdir(), "nb_worker_" + process.pid + ".mjs");
copyFileSync(join(here, "..", "..", "gateway", "worker.js"), copy);
const worker = (await import(pathToFileURL(copy).href)).default;

let n = 0;
const fails = [];
function check(name, cond, extra = "") {
  n++;
  console.log((cond ? "ok   " : "FAIL ") + name + (extra !== "" ? "  " + JSON.stringify(extra) : ""));
  if (!cond) fails.push(name);
}
const sha = (s) => createHash("sha256").update(s).digest("hex");
const env = { OPENAI_KEY: "sk-company-1", STUDIO_KEY: "", ARK_KEY: "ark-company-2",
              RETIRED_KEY_HASHES: sha("old-3") + ", nothex", TICKET_HASHES: "" };
let ipN = 0;
async function raw(body, ip, path = "/personal/check", method = "POST") {
  const init = { method, headers: { "Content-Type": "application/json", "CF-Connecting-IP": ip || "10.0.0." + (++ipN) } };
  if (method !== "GET") init.body = typeof body === "string" ? body : JSON.stringify(body);
  return worker.fetch(new Request("https://gw.test" + path, init), env, { waitUntil() {} });
}
async function call(body) {
  const r = await raw(body);
  return { status: r.status, ...(await r.json().catch(() => ({}))) };
}

let r = await call({ p: sha("sk-company-1").slice(0, 8) });
check("match current key", r.status === 200 && r.ok === true && r.hashes.includes(sha("sk-company-1")), r);
r = await call({ p: sha("ark-company-2").slice(0, 8) });
check("match another current key", r.hashes && r.hashes.includes(sha("ark-company-2")));
r = await call({ p: sha("old-3").slice(0, 8) });
check("match retired", r.hashes && r.hashes.includes(sha("old-3")));
r = await call({ p: sha("sk-mine").slice(0, 8) });
check("someone else's key: nothing of theirs comes back", r.status === 200 && Array.isArray(r.hashes) && !r.hashes.includes(sha("sk-mine")));
check("only hashes with that prefix", (r.hashes || [null]).every((h) => h.startsWith(sha("sk-mine").slice(0, 8))));
check("bad prefix 400", (await raw({ p: "xyz" })).status === 400);
check("uppercase / long prefix 400", (await raw({ p: "ABCDEF12" })).status === 400 && (await raw({ p: "abcdef123" })).status === 400);
check("a key instead of a prefix 400", (await raw({ key: "sk-company-1" })).status === 400);
check("broken body 400", (await raw("{not json")).status === 400);
check("GET not served", (await raw(null, undefined, "/personal/check", "GET")).status === 404);
let last = 0;
for (let i = 0; i < 61; i++) last = (await raw({ p: "00000000" }, "10.9.9.9")).status;
check("rate limit 429", last === 429, last);
check("other addresses unaffected", (await raw({ p: "00000000" }, "10.9.9.10")).status === 200);
check("other routes untouched", (await raw(null, undefined, "/health", "GET")).status === 200);
console.log("\nchecks " + n + ", failed " + fails.length);
for (const f of fails) console.log("  - " + f);
process.exit(fails.length ? 1 : 0);
