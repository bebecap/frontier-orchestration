// node --test --experimental-test-module-mocks
// Every handler answers only on its exact path. Vercel also sends /api/e/, /api/e.js and the
// like to the same function, and a Firewall rule that matches the exact path does not cover them.
import { test, mock, beforeEach } from "node:test";
import assert from "node:assert/strict";

assert.equal(typeof mock.module, "function",
  "run with: node --test --experimental-test-module-mocks (node >= 22.3)");

let calls = [];
function fakeSql(strings, ...values) {
  calls.push({ text: strings.join("$"), values });
  return Promise.resolve(strings.join("$").includes("admitted") ? [{ admitted: 1 }] : []);
}
// /api/e sends its two statements as one transaction (sql.transaction, as in the Neon driver).
fakeSql.transaction = (queries) => Promise.all(queries);
mock.module(new URL("../api/_db.js", import.meta.url).href, { namedExports: { db: () => fakeSql } });
const e = await import("../api/e.js");
const stats = await import("../api/stats.js");
const prune = await import("../api/prune.js");

const KEY = "test-public-key", STATS = "test-stats-token", CRON = "test-cron-secret";
const BASE = "https://collector.test";
const EVENT = JSON.stringify({
  api_key: KEY, event: "fo_doctor", distinct_id: "00000000-0000-4000-8000-000000000000",
  properties: { schema: 4, os: "darwin" }, timestamp: "2026-11-16T00:00:00Z",
});
const postE = (path, type = "application/json") => e.POST(new Request(BASE + path, {
  method: "POST", headers: { "content-type": type }, body: EVENT,
}));
const get = (handler, path, token) =>
  handler.GET(new Request(BASE + path, { headers: token ? { authorization: `Bearer ${token}` } : {} }));

beforeEach(() => {
  process.env.FO_PUBLIC_KEY = KEY;
  process.env.STATS_TOKEN = STATS;
  process.env.CRON_SECRET = CRON;
  calls = [];
});

const E_ALIASES = ["/api/e/", "/api/e.js", "/api/e/x", "/API/e", "/api/E", "/api/e.js/", "/api/%65"];

test("POST /api/e: an alias path is 404 with an empty body and no database call", async () => {
  for (const path of E_ALIASES) {
    const res = await postE(path);
    assert.equal(res.status, 404, path);
    assert.equal(await res.text(), "", path);
  }
  assert.equal(calls.length, 0);
});

test("POST /api/e: the path check runs before the content type and size checks", async () => {
  assert.equal((await postE("/api/e/", "text/plain")).status, 404);
  const big = await e.POST(new Request(BASE + "/api/e.js", {
    method: "POST", headers: { "content-type": "application/json", "content-length": "9000" }, body: EVENT,
  }));
  assert.equal(big.status, 404);
  assert.equal(calls.length, 0);
});

test("GET /api/e: an alias path is 404 with an empty body", async () => {
  for (const path of E_ALIASES) {
    const res = await get(e, path);
    assert.equal(res.status, 404, path);
    assert.equal(await res.text(), "", path);
  }
});

test("GET /api/stats: an alias path is 404 with no-store, before auth and with no database call", async () => {
  for (const path of ["/api/stats/", "/api/stats.js", "/api/stats/x", "/API/stats", "/api/stats.js?days=7"]) {
    for (const token of [STATS, null]) {
      const res = await get(stats, path, token);
      assert.equal(res.status, 404, `${path} ${token}`);
      assert.equal(res.headers.get("cache-control"), "no-store", path);
      assert.equal(await res.text(), "", path);
    }
  }
  assert.equal(calls.length, 0);
});

test("GET /api/prune: an alias path is 404, before auth and with no database call", async () => {
  for (const path of ["/api/prune/", "/api/prune.js", "/api/prune/x", "/API/prune"]) {
    for (const token of [CRON, null]) {
      const res = await get(prune, path, token);
      assert.equal(res.status, 404, `${path} ${token}`);
      assert.equal(await res.text(), "", path);
    }
  }
  assert.equal(calls.length, 0);
});

test("the exact paths still work, with or without a query string", async () => {
  assert.equal((await postE("/api/e")).status, 204);
  assert.equal((await postE("/api/e?x=1")).status, 204);
  assert.equal((await postE("/api/e", "text/plain")).status, 415);
  assert.equal((await get(e, "/api/e")).status, 200);
  assert.equal((await get(stats, "/api/stats?days=7", STATS)).status, 200);
  assert.equal((await get(stats, "/api/stats")).status, 401);
  assert.equal((await get(prune, "/api/prune", CRON)).status, 200);
  assert.equal((await get(prune, "/api/prune")).status, 401);
});
