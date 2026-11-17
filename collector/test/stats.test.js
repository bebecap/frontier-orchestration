// node --test --experimental-test-module-mocks
// GET /api/stats with a stubbed db(): no network, no database, no npm install needed.
import { test, mock, beforeEach } from "node:test";
import assert from "node:assert/strict";

assert.equal(typeof mock.module, "function",
  "run with: node --test --experimental-test-module-mocks (node >= 22.3)");

// The stub stands in for _db.js, so the Neon driver is never loaded.
let calls = [], answer = () => [], fail = false;
function fakeSql(strings, ...values) {
  const text = strings.join("$");
  calls.push({ text, values });
  if (fail) return Promise.reject(new Error("database down"));
  return Promise.resolve(answer(text));
}
mock.module(new URL("../api/_db.js", import.meta.url).href, { namedExports: { db: () => fakeSql } });
process.env.FO_BUDGET_PER_HOUR = "100";  // read once, when the module loads (as in e.js)
const { GET } = await import("../api/stats.js");
const { budgetPerHour } = await import("../api/_budget.js");

const TOKEN = "test-stats-token";
const req = (query = "", auth = `Bearer ${TOKEN}`) =>
  new Request(`https://collector.test/api/stats${query}`, { headers: auth ? { authorization: auth } : {} });

const ROWS = [
  { field: "event", value: "route", n: 7 },
  { field: "event", value: "ask", n: 3 },
  { field: "os", value: "darwin", n: 8 },
  { field: "os", value: "linux", n: 2 },
  { field: "tasks", value: "10", n: 1 },
  { field: "tasks", value: "2", n: 1 },
];
const DAILY = [
  { day: "2026-11-13", value: "ask", n: 1 },
  { day: "2026-11-13", value: "route", n: 4 },
  { day: "2026-11-15", value: "ask", n: 2 },
  { day: "2026-11-15", value: "route", n: 3 },
];
const BUDGET = [
  { hour: "2026-11-15T10", n: 40 },
  { hour: "2026-11-15T11", n: 100 },
  { hour: "2026-11-15T12", n: 99 },
];
const isSeriesQuery = (text) => text.includes("to_char(day");
const isBudgetQuery = (text) => text.includes("fo_budget");

// The response before this change, computed the old way, so the old fields are proven unchanged.
function oldBody(days, rows) {
  const counts = Object.create(null);
  for (const r of rows) { (counts[r.field] ??= Object.create(null))[r.value] = r.n; }
  return { days, events: Object.values(counts.event || {}).reduce((a, b) => a + b, 0), counts };
}

beforeEach(() => {
  process.env.STATS_TOKEN = TOKEN;
  calls = [];
  fail = false;
  answer = (text) => (isBudgetQuery(text) ? BUDGET : isSeriesQuery(text) ? DAILY : ROWS);
});

test("401 with no-store and no database call without the right token", async () => {
  for (const auth of [null, "Bearer wrong", `bearer ${TOKEN}`, TOKEN, `Bearer ${TOKEN}x`]) {
    const res = await GET(req("", auth));
    assert.equal(res.status, 401, String(auth));
    assert.equal(res.headers.get("cache-control"), "no-store");
    assert.equal(await res.text(), "");
  }
  delete process.env.STATS_TOKEN;  // an unset token must never mean "open"
  const res = await GET(req("", "Bearer "));
  assert.equal(res.status, 401);
  assert.equal(res.headers.get("cache-control"), "no-store");
  assert.equal(calls.length, 0);
});

test("old fields unchanged, plus series and fields", async () => {
  const res = await GET(req("?days=7"));
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("cache-control"), "no-store");
  assert.match(res.headers.get("content-type"), /application\/json/);
  const body = await res.json();
  const { series, fields, budget, ...old } = body;
  assert.deepEqual(old, JSON.parse(JSON.stringify(oldBody(7, ROWS))));
  assert.deepEqual(Object.keys(body), ["days", "events", "counts", "series", "fields", "budget"]);
  assert.equal(body.events, 10);
  assert.deepEqual(series, [
    { day: "2026-11-13", counts: { ask: 1, route: 4 } },
    { day: "2026-11-15", counts: { ask: 2, route: 3 } },
  ]);
  assert.deepEqual(fields, ["event", "os", "tasks"]);
  // The original aggregate query is still the one that makes "counts".
  const agg = calls.find((c) => !isSeriesQuery(c.text) && !isBudgetQuery(c.text));
  assert.match(agg.text, /SELECT field, value, sum\(n\)::int AS n FROM fo_counts/);
  assert.match(agg.text, /GROUP BY field, value ORDER BY field, n DESC/);
  const per = calls.find((c) => isSeriesQuery(c.text));
  assert.match(per.text, /field = 'event'/);
  assert.match(per.text, /ORDER BY day/);
});

test("series keeps the database's day order and folds rows of one day together", async () => {
  answer = (text) => (isSeriesQuery(text)
    ? [{ day: "2026-11-01", value: "setup", n: 2 }, { day: "2026-11-02", value: "ask", n: 1 },
       { day: "2026-11-02", value: "route", n: 5 }]
    : [{ field: "event", value: "route", n: 5 }, { field: "event", value: "setup", n: 2 },
       { field: "event", value: "ask", n: 1 }]);
  const body = await (await GET(req())).json();
  assert.deepEqual(body.series.map((p) => p.day), ["2026-11-01", "2026-11-02"]);
  assert.deepEqual(body.series[1].counts, { ask: 1, route: 5 });
  assert.deepEqual(body.fields, ["event"]);
});

test("empty window: zero events, empty series and fields", async () => {
  answer = () => [];
  const body = await (await GET(req("?days=90"))).json();
  assert.deepEqual(body, { days: 90, events: 0, counts: {}, series: [], fields: [], budget: { per_hour: 100, hours: [] } });
});

test("days is clamped to 1..365 and reaches both queries", async () => {
  const cases = [["", 30], ["?days=7", 7], ["?days=90", 90], ["?days=1", 1], ["?days=-5", 1],
    ["?days=0", 30], ["?days=366", 365], ["?days=99999", 365], ["?days=abc", 30], ["?days=12.9", 12]];
  for (const [q, want] of cases) {
    calls = [];
    const body = await (await GET(req(q))).json();
    assert.equal(body.days, want, q);
    const counted = calls.filter((c) => !isBudgetQuery(c.text));
    assert.equal(counted.length, 2, q);
    for (const c of counted) assert.deepEqual(c.values, [want], q);
  }
});

test("a database error is a 503 with no-store, not a cached 500 page", async () => {
  fail = true;
  const res = await GET(req());
  assert.equal(res.status, 503);
  assert.equal(res.headers.get("cache-control"), "no-store");
});

test("budget: use per UTC hour for the last 48 hours, oldest first, saturated at the limit", async () => {
  const body = await (await GET(req("?days=7"))).json();
  assert.deepEqual(body.budget, {
    per_hour: 100,
    hours: [
      { hour: "2026-11-15T10", n: 40, saturated: false },
      { hour: "2026-11-15T11", n: 100, saturated: true },
      { hour: "2026-11-15T12", n: 99, saturated: false },
    ],
  });
  const q = calls.filter((c) => isBudgetQuery(c.text));
  assert.equal(q.length, 1);
  assert.match(q[0].text, /SELECT hour, n FROM fo_budget/);
  assert.match(q[0].text, /interval '47 hours'/);  // this hour and the 47 before it
  assert.match(q[0].text, /ORDER BY hour/);
  assert.deepEqual(q[0].values, [], "the window does not depend on days");
});

test("a budget query error is a 503 too", async () => {
  answer = (text) => (isBudgetQuery(text) ? Promise.reject(new Error("budget down")) : []);
  const res = await GET(req());
  assert.equal(res.status, 503);
  assert.equal(res.headers.get("cache-control"), "no-store");
});

test("budgetPerHour: a positive integer, else the default 2000 (the rule e.js uses)", () => {
  const cases = [[null, 2000], ["", 2000], ["abc", 2000], ["0", 2000], ["-5", 2000], ["1.5", 2000],
    ["1", 1], ["500", 500], ["2000", 2000]];
  for (const [raw, want] of cases) assert.equal(budgetPerHour(raw), want, String(raw));
});
