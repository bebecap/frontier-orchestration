// node --test --experimental-test-module-mocks
// POST /api/e with a stubbed db(). The real-PostgreSQL tests run the real SQL on a throwaway local
// PostgreSQL cluster (initdb, pg_ctl and psql on PATH); without them they are skipped, and say so.
// Each transaction runs in its own psql session, so concurrent requests are really concurrent.
import { test, mock, beforeEach, afterEach, before, after } from "node:test";
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

assert.equal(typeof mock.module, "function",
  "run with: node --test --experimental-test-module-mocks (node >= 22.3)");

// "stub" records the call and admits; "full" answers "over budget"; "fail" is a statement
// error; "pg" runs the statements on the local cluster. Like the Neon driver, a tagged query
// runs only when it is awaited, and sql.transaction([...], opts) runs its queries as one
// transaction in one round trip. `calls` has one entry per round trip.
let mode = "stub", calls = [];
const stubRows = () => [{ admitted: mode === "full" ? 0 : 1 }];
function fakeSql(strings, ...values) {
  const query = { strings, values };
  query.then = (ok, bad) => {
    calls.push({ text: strings.join("$"), values });
    const run = mode === "pg" ? runOnPg([query])
      : mode === "fail" ? Promise.reject(new Error("database down")) : Promise.resolve([stubRows()]);
    return run.then((results) => results[0]).then(ok, bad);
  };
  return query;
}
fakeSql.transaction = (queries, opts) => {
  calls.push({ text: queries.map((q) => q.strings.join("$")).join("\n;\n"),
    values: queries.flatMap((q) => q.values), queries, opts });
  if (mode === "pg") return runOnPg(queries, opts);
  if (mode === "fail") return Promise.reject(new Error("database down"));
  return Promise.resolve(queries.map((q, i) => (i === queries.length - 1 ? stubRows() : [])));
};
mock.module(new URL("../api/_db.js", import.meta.url).href, { namedExports: { db: () => fakeSql } });
const { POST, MAX_VALUES_PER_FIELD, OVERFLOW } = await import("../api/e.js");
const { BUDGET_PER_HOUR } = await import("../api/_budget.js");
const stats = await import("../api/stats.js");

const KEY = "test-public-key";
const event = (props = {}) => ({
  api_key: KEY, event: "fo_doctor", distinct_id: "00000000-0000-4000-8000-000000000000",
  properties: { schema: 4, os: "darwin", ...props }, timestamp: "2026-11-16T00:00:00Z",
});
const post = (body, type = "application/json") =>
  POST(new Request("https://collector.test/api/e", {
    method: "POST", headers: type === null ? {} : { "content-type": type },
    body: typeof body === "string" ? body : JSON.stringify(body),
  }));

beforeEach(() => { process.env.FO_PUBLIC_KEY = KEY; mode = "stub"; calls = []; });
afterEach(() => { mock.timers.reset(); });

test("a valid event with application/json is admitted (charset and case allowed)", async () => {
  for (const type of ["application/json", "application/json; charset=utf-8", "Application/JSON"]) {
    calls = [];
    const res = await post(event(), type);
    assert.equal(res.status, 204, type);
    assert.equal(calls.length, 1, type);
  }
});

test("any other content type is 415 and never reaches the database", async () => {
  // text/plain is what a cross-site page can send without a CORS preflight.
  for (const type of ["text/plain", "text/plain;charset=UTF-8", "application/x-www-form-urlencoded",
    "multipart/form-data; boundary=x", "application/jsonx", "", null]) {
    const res = await post(event(), type);
    assert.equal(res.status, 415, String(type));
  }
  assert.equal(calls.length, 0);
});

test("oversized, malformed and invalid events never reach the database", async () => {
  assert.equal((await post(JSON.stringify({ pad: "x".repeat(9000) }))).status, 413);
  assert.equal((await post("{")).status, 400);
  assert.equal((await post(event({ schema: 3 }))).status, 400);
  assert.equal((await post({ ...event(), event: "fo_unknown" })).status, 400);
  assert.equal((await post({ ...event(), api_key: "wrong" })).status, 400);
  assert.equal(calls.length, 0);
});

// A body stream that records whether anything read it. highWaterMark 0: no read before asked.
function trackedBody(text) {
  const state = { read: false };
  state.stream = new ReadableStream({
    pull(c) { state.read = true; c.enqueue(new TextEncoder().encode(text)); c.close(); },
  }, { highWaterMark: 0 });
  return state;
}
const postLength = (length, body, type = "application/json") =>
  POST(new Request("https://collector.test/api/e", {
    method: "POST", headers: { "content-type": type, "content-length": String(length) },
    body: body.stream, duplex: "half",
  }));

test("a Content-Length over 8192 is 413 and the body is never read", async () => {
  for (const length of [8193, 9000, 1e9]) {
    const body = trackedBody(JSON.stringify(event()));
    const res = await postLength(length, body);
    assert.equal(res.status, 413, String(length));
    assert.equal(body.read, false, `${length}: the body must not be read`);
  }
  assert.equal(calls.length, 0);
});

test("the 415 comes before the Content-Length 413", async () => {
  const body = trackedBody("x");
  assert.equal((await postLength(9000, body, "text/plain")).status, 415);
  assert.equal(body.read, false);
});

test("a Content-Length of 8192 or less is read, and the byte check still runs after", async () => {
  const ok = trackedBody(JSON.stringify(event()));
  assert.equal((await postLength(8192, ok)).status, 204);
  assert.equal(ok.read, true);
  // The header can lie: the real size is checked after reading.
  const lie = trackedBody(JSON.stringify({ pad: "x".repeat(9000) }));
  assert.equal((await postLength(10, lie)).status, 413);
  assert.equal(calls.length, 1);
});

test("after a budget 429 the same hour is 429 with no database call; a new hour asks again", async () => {
  mock.timers.enable({ apis: ["Date"], now: Date.parse("2031-01-01T05:10:00Z") });
  mode = "full";
  assert.equal((await post(event())).status, 429);
  assert.equal(calls.length, 1);
  mode = "stub";  // even when the database would now admit, this instance does not ask
  mock.timers.setTime(Date.parse("2031-01-01T05:59:59Z"));
  assert.equal((await post(event())).status, 429);
  assert.equal((await post(event())).status, 429);
  assert.equal(calls.length, 1, "no database call while the hour is full");
  // An invalid event is still a 400, not a 429.
  assert.equal((await post(event({ schema: 3 }))).status, 400);
  mock.timers.setTime(Date.parse("2031-01-01T06:00:00Z"));
  assert.equal((await post(event())).status, 204);
  assert.equal(calls.length, 2, "a new hour calls the database again");
});

test("a 503 does not mark the hour full", async () => {
  mock.timers.enable({ apis: ["Date"], now: Date.parse("2031-02-01T05:10:00Z") });
  mode = "fail";
  assert.equal((await post(event())).status, 503);
  assert.equal((await post(event())).status, 503);
  mode = "stub";
  assert.equal((await post(event())).status, 204);
  assert.equal(calls.length, 3);
});

test("the statement carries the per-field cap and the overflow value", async () => {
  await post(event({ fo_version: "1.2.3" }));
  assert.equal(calls.length, 1);
  assert.ok(calls[0].values.includes(MAX_VALUES_PER_FIELD));
  assert.ok(calls[0].values.includes(OVERFLOW));
  assert.match(calls[0].text, /count\(\*\) FROM fo_counts/);
});

test("one round trip: per-(day, field) locks first, then the counts, in one READ COMMITTED transaction", async () => {
  await post(event({ fo_version: "1.2.3" }));
  assert.equal(calls.length, 1);
  const { queries, opts } = calls[0];
  assert.deepEqual(opts, { isolationLevel: "ReadCommitted" });
  assert.equal(queries.length, 2);
  const [lock, counts] = queries.map((q) => q.strings.join("$"));
  assert.match(lock, /pg_advisory_xact_lock/);
  assert.match(lock, /ORDER BY k/);
  assert.doesNotMatch(lock, /fo_counts \(|fo_budget/);
  assert.match(counts, /INSERT INTO fo_counts/);
  // The lock covers every field the counts statement writes.
  assert.deepEqual(queries[0].values[1], ["event", "os", "fo_version"]);
  assert.deepEqual(queries[1].values.find(Array.isArray), ["event", "os", "fo_version"]);
});

// ---------- real PostgreSQL ----------

const have = (bin) => spawnSync(bin, ["--version"], { encoding: "utf8" }).status === 0;
const PG = have("initdb") && have("pg_ctl") && have("psql");
let dir = null, sock = null;
const port = String(50000 + (process.pid % 10000));

function psql(args, input) {
  const r = spawnSync("psql", ["-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-h", sock, "-p", port,
    "-U", "postgres", "-d", "postgres", ...args], { encoding: "utf8", input });
  if (r.status !== 0) throw new Error(`psql failed: ${r.stderr}`);
  return r.stdout.trim();
}

// Like the Neon driver: $1..$n placeholders, every value sent as text (arrays as array
// literals), types inferred by the server. A transaction runs in one psql session (BEGIN,
// PREPARE and EXECUTE each query, COMMIT) that is its own process, so concurrent requests
// are concurrent sessions. `barrier`, when set, makes each session wait on a shared advisory
// lock before BEGIN (outside the transaction under test), so a test can release them all at once.
const quote = (s) => `'${String(s).replace(/'/g, "''")}'`;
const asText = (v) => Array.isArray(v)
  ? `{${v.map((x) => `"${String(x).replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`).join(",")}}`
  : String(v);
const MARK = "--fo-result--";
let barrier = null;
function runOnPg(queries, opts) {
  const iso = opts?.isolationLevel === "ReadCommitted" ? " ISOLATION LEVEL READ COMMITTED" : opts?.isolationLevel === "RepeatableRead" ? " ISOLATION LEVEL REPEATABLE READ" : "";
  let script = barrier === null ? "" : `SELECT pg_advisory_lock_shared(${barrier});\nSELECT pg_advisory_unlock_shared(${barrier});\n`;
  script += `\\pset format csv\nBEGIN${iso};\n`;
  queries.forEach(({ strings, values }, n) => {
    const text = strings.reduce((acc, s, i) => acc + s + (i < values.length ? `$${i + 1}` : ""), "");
    const args = values.length ? `(${values.map((v) => quote(asText(v))).join(", ")})` : "";
    script += `PREPARE q${n} AS ${text};\n\\echo ${MARK}\nEXECUTE q${n}${args};\n`;
  });
  script += "COMMIT;\n";
  return new Promise((resolve, reject) => {
    const p = spawn("psql", ["-X", "-q", "-v", "ON_ERROR_STOP=1", "-h", sock, "-p", port,
      "-U", "postgres", "-d", "postgres", "-f", "-"], { stdio: ["pipe", "pipe", "pipe"] });
    let out = "", err = "";
    p.stdout.on("data", (d) => { out += d; });
    p.stderr.on("data", (d) => { err += d; });
    p.on("error", reject);
    p.on("close", (code) => {
      if (code !== 0) return reject(new Error(`psql failed: ${err}`));
      // Rows come back as objects; integer-looking cells as numbers (no test value has a comma).
      resolve(out.split(MARK + "\n").slice(1).map((part) => {
        const [head, ...lines] = part.split("\n").filter((l) => l !== "");
        const cols = (head || "").split(",");
        return lines.map((l) => Object.fromEntries(l.split(",").map((v, i) => [cols[i], /^\d+$/.test(v) ? Number(v) : v])));
      }));
    });
    p.stdin.end(script);
  });
}

// Holds an exclusive advisory lock in its own session until release(): every session that
// runOnPg starts while `barrier` is set waits for it.
async function holdBarrier(key) {
  const p = spawn("psql", ["-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-h", sock, "-p", port,
    "-U", "postgres", "-d", "postgres"], { stdio: ["pipe", "pipe", "inherit"] });
  let out = "";
  const held = new Promise((resolve) => p.stdout.on("data", (d) => { out += d; if (out.includes("held")) resolve(); }));
  p.stdin.write(`SELECT pg_advisory_lock(${key});\n\\echo held\n`);
  await held;
  const closed = new Promise((resolve) => p.on("close", resolve));
  return async () => { p.stdin.end(`SELECT pg_advisory_unlock(${key});\n`); await closed; };
}

// Fires `bodies` as concurrent POSTs that all start their transactions at the same moment.
async function concurrentPosts(bodies) {
  const key = 4242;
  const release = await holdBarrier(key);
  barrier = key;
  let pending;
  try {
    pending = bodies.map((b) => post(b));
    const deadline = Date.now() + 60_000;
    for (;;) {  // wait until every session waits on the barrier
      const waiting = Number(psql(["-c", `SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = ${key} AND NOT granted`]));
      if (waiting === bodies.length) break;
      if (Date.now() > deadline) throw new Error(`only ${waiting} of ${bodies.length} sessions reached the barrier`);
      await new Promise((r) => setTimeout(r, 50));
    }
  } finally {
    barrier = null;
    await release();
  }
  return (await Promise.all(pending)).map((r) => r.status);
}

before(() => {
  if (!PG) return;
  // A Unix socket path must stay under about 100 bytes; a long TMPDIR falls back to /tmp.
  dir = mkdtempSync(join(tmpdir().length > 60 ? "/tmp" : tmpdir(), "fo-pg-"));
  sock = dir;
  const init = spawnSync("initdb", ["-D", join(dir, "data"), "-U", "postgres", "--auth=trust", "-E", "UTF8",
    "--no-locale"], { encoding: "utf8" });
  assert.equal(init.status, 0, init.stderr);
  const start = spawnSync("pg_ctl", ["-D", join(dir, "data"), "-w", "-l", join(dir, "log"), "-o",
    `-k ${sock} -p ${port} -c listen_addresses= -c max_connections=200`, "start"], { encoding: "utf8" });
  assert.equal(start.status, 0, start.stderr);
  psql(["-f", new URL("../schema.sql", import.meta.url).pathname]);
});

after(() => {
  if (!dir) return;
  spawnSync("pg_ctl", ["-D", join(dir, "data"), "-m", "immediate", "stop"], { encoding: "utf8" });
  rmSync(dir, { recursive: true, force: true });
});

test("real PostgreSQL: made-up values cannot grow a field past the cap", { skip: !PG && "no local PostgreSQL" }, async () => {
  mode = "pg";
  psql(["-c", "TRUNCATE fo_counts, fo_budget"]);
  const extra = 25;
  for (let i = 0; i < MAX_VALUES_PER_FIELD + extra; i++) {
    const res = await post(event({ fo_version: `9.9.${i}` }));
    assert.equal(res.status, 204);
  }
  // A value seen before the cap keeps counting under its own name.
  assert.equal((await post(event({ fo_version: "9.9.0" }))).status, 204);
  const day = new Date().toISOString().slice(0, 10);
  const rows = psql(["-c", `SELECT count(*) FROM fo_counts WHERE day = '${day}' AND field = 'fo_version'`]);
  assert.equal(Number(rows), MAX_VALUES_PER_FIELD + 1);  // the cap plus the overflow row
  assert.equal(Number(psql(["-c", `SELECT n FROM fo_counts WHERE field = 'fo_version' AND value = '${OVERFLOW}'`])), extra);
  assert.equal(Number(psql(["-c", "SELECT n FROM fo_counts WHERE field = 'fo_version' AND value = '9.9.0'"])), 2);
  // Every admitted event still counts once, and capped fields do not touch other fields.
  const total = MAX_VALUES_PER_FIELD + extra + 1;
  assert.equal(Number(psql(["-c", "SELECT n FROM fo_counts WHERE field = 'event' AND value = 'doctor'"])), total);
  assert.equal(Number(psql(["-c", "SELECT n FROM fo_counts WHERE field = 'os' AND value = 'darwin'"])), total);
  assert.equal(Number(psql(["-c", "SELECT sum(n) FROM fo_budget"])), total);
});

test("real PostgreSQL: stats lists budget use for the last 48 hours, oldest first", { skip: !PG && "no local PostgreSQL" }, async () => {
  mode = "pg";
  psql(["-c", "TRUNCATE fo_counts, fo_budget"]);
  // Rows 0, 1, 47, 48 and 72 hours old, made by the server clock; n = the age, 2000 = saturated.
  psql(["-c", `INSERT INTO fo_budget (hour, n) SELECT to_char((now() AT TIME ZONE 'utc') - make_interval(hours => h), 'YYYY-MM-DD"T"HH24'),
    CASE WHEN h = 1 THEN 2000 ELSE h END FROM unnest('{0,1,47,48,72}'::int[]) AS h`]);
  const want = (age) => psql(["-c", `SELECT to_char((now() AT TIME ZONE 'utc') - make_interval(hours => ${age}), 'YYYY-MM-DD"T"HH24')`]);
  process.env.STATS_TOKEN = "pg-stats-token";
  const res = await stats.GET(new Request("https://collector.test/api/stats", { headers: { authorization: "Bearer pg-stats-token" } }));
  assert.equal(res.status, 200);
  const { budget } = await res.json();
  assert.equal(budget.per_hour, 2000);
  assert.deepEqual(budget.hours, [
    { hour: want(47), n: 47, saturated: false },
    { hour: want(1), n: 2000, saturated: true },
    { hour: want(0), n: 0, saturated: false },
  ]);
});

test("real PostgreSQL: 64 concurrent new values at 99 end with exactly 100 values plus (overflow)", { skip: !PG && "no local PostgreSQL" }, async () => {
  mode = "pg";
  psql(["-c", "TRUNCATE fo_counts, fo_budget"]);
  const day = new Date().toISOString().slice(0, 10);
  psql(["-c", `INSERT INTO fo_counts (day, field, value, n)
    SELECT '${day}'::date, 'fo_version', '8.8.' || i, 1 FROM generate_series(1, ${MAX_VALUES_PER_FIELD - 1}) AS i`]);
  const n = 64;
  const statuses = await concurrentPosts(Array.from({ length: n }, (_, i) => event({ fo_version: `7.7.${i}` })));
  assert.deepEqual(statuses, Array(n).fill(204));
  const count = (where) => Number(psql(["-c", `SELECT count(*) FROM fo_counts WHERE day = '${day}' AND field = 'fo_version' AND ${where}`]));
  assert.equal(count(`value <> '${OVERFLOW}'`), MAX_VALUES_PER_FIELD, "ordinary fo_version values");
  assert.equal(count(`value = '${OVERFLOW}'`), 1);
  assert.equal(Number(psql(["-c", `SELECT n FROM fo_counts WHERE day = '${day}' AND field = 'fo_version' AND value = '${OVERFLOW}'`])), n - 1);
  assert.equal(Number(psql(["-c", `SELECT sum(n) FROM fo_counts WHERE day = '${day}' AND field = 'fo_version' AND value LIKE '7.7.%'`])), 1);
  // Every event counted once in the other fields.
  assert.equal(Number(psql(["-c", "SELECT n FROM fo_counts WHERE field = 'event' AND value = 'doctor'"])), n);
  assert.equal(Number(psql(["-c", "SELECT sum(n) FROM fo_budget"])), n);
});

test("real PostgreSQL: 64 concurrent events with 10 left in the hourly budget admit exactly 10", { skip: !PG && "no local PostgreSQL" }, async () => {
  mode = "pg";
  psql(["-c", "TRUNCATE fo_counts, fo_budget"]);
  // A pinned hour: this test leaves the instance's "hour full" mark on it, not on the real hour.
  mock.timers.enable({ apis: ["Date"], now: Date.parse("2031-03-01T07:10:00Z") });
  psql(["-c", `INSERT INTO fo_budget (hour, n) VALUES ('2031-03-01T07', ${BUDGET_PER_HOUR - 10})`]);
  const n = 64;
  const statuses = await concurrentPosts(Array.from({ length: n }, () => event()));
  assert.equal(statuses.filter((s) => s === 204).length, 10);
  assert.equal(statuses.filter((s) => s === 429).length, n - 10);
  assert.equal(Number(psql(["-c", "SELECT n FROM fo_budget WHERE hour = '2031-03-01T07'"])), BUDGET_PER_HOUR);
  assert.equal(Number(psql(["-c", "SELECT n FROM fo_counts WHERE day = '2031-03-01' AND field = 'event' AND value = 'doctor'"])), 10);
});
