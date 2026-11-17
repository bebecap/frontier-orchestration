// POST /api/e : one telemetry event. Body: {api_key, event, distinct_id, properties, timestamp}.
// No event row and no id is stored (the client sends a fresh random id per event; it is ignored).
// One transaction of two statements, sent in one round trip, admits the event against an hourly
// budget and, only if admitted, adds 1 to the day's counter for the event name and for each
// allowlisted property value. Over budget, or on a statement error, no counter changes.
// It reads only the Content-Type and Content-Length headers (see POST) and stores no header and
// no IP. A Vercel Firewall rule also limits each IP; it matches the exact path /api/e, so every
// other path that reaches this function is a 404 (see _path.js).
// Each (day, field) keeps at most MAX_VALUES_PER_FIELD distinct values; a further new value is
// counted under OVERFLOW, so a stranger cannot grow the table without limit with made-up
// versions or integers. The cap holds under concurrency: the first statement takes a
// transaction-level advisory lock for each (day, field) of the event, in sorted order (so two
// events cannot deadlock), and the second statement counts the existing values in a fresh READ
// COMMITTED snapshot while it holds those locks. No other writer can add a value to that
// (day, field) until this transaction commits.
import { clean } from "./_schema.js";
import { db } from "./_db.js";
import { exactPath } from "./_path.js";
import { BUDGET_PER_HOUR } from "./_budget.js";

const MAX_BYTES = 8192;
export const MAX_VALUES_PER_FIELD = 100;
// No allowlisted value can be this (it fails every enum, integer and string rule).
export const OVERFLOW = "(overflow)";
// The UTC hour ('YYYY-MM-DDTHH') for which the statement said "over budget", in this function
// instance only. Later events in that hour get 429 with no database call. Set only by a budget
// 429, never by a 503. A new hour no longer matches it, so the database is asked again.
let fullHour = null;

export async function POST(request) {
  if (!exactPath(request, "/api/e")) return new Response(null, { status: 404 });
  // Only application/json. A web page can send a cross-site text/plain POST without a CORS
  // preflight, so any visitor's browser could be made to send events from its own IP. With
  // application/json the browser must ask first (preflight), and this endpoint never allows it.
  const type = (request.headers.get("content-type") || "").split(";")[0].trim().toLowerCase();
  if (type !== "application/json") return new Response(null, { status: 415 });
  // A declared size over the limit is refused before the body is read. The header can be
  // missing or wrong, so the real size is checked again after reading.
  const length = request.headers.get("content-length");
  if (length !== null && Number(length) > MAX_BYTES) return new Response(null, { status: 413 });
  const bytes = new Uint8Array(await request.arrayBuffer());
  if (bytes.byteLength > MAX_BYTES) return new Response(null, { status: 413 });  // bytes, not UTF-16 units
  let body;
  try { body = JSON.parse(new TextDecoder().decode(bytes)); } catch { return new Response(null, { status: 400 }); }
  let ev;
  try { ev = clean(body, process.env.FO_PUBLIC_KEY); } catch { ev = null; }
  if (!ev) return new Response(null, { status: 400 });

  const now = new Date().toISOString();
  const hour = now.slice(0, 13), day = now.slice(0, 10);
  if (fullHour === hour) return new Response(null, { status: 429 });
  const fields = ["event", ...Object.keys(ev.props)];
  const values = [ev.event, ...Object.values(ev.props).map(String)];
  let admitted;
  try {
    // One transaction, one round trip: admission and counters commit together or not at all.
    // READ COMMITTED gives the second statement a new snapshot, taken after the first statement
    // holds the locks, so its count(*) sees every value another event committed. (Under
    // REPEATABLE READ the snapshot would be older than the locks and the cap could be passed.)
    // The locks are taken before admission: an over-budget event holds them only for the few
    // milliseconds of this transaction. The conditional DO UPDATE changes no counter once the
    // hour's budget is spent.
    const sql = db();
    const results = await sql.transaction([
      sql`SELECT pg_advisory_xact_lock(hashtext('fo_counts'), hashtext(s.k))
        FROM (SELECT DISTINCT ${day} || ':' || f AS k FROM unnest(${fields}::text[]) AS f ORDER BY k) AS s`,
      sql`
      WITH adm AS (
        INSERT INTO fo_budget (hour, n) VALUES (${hour}, 1)
        ON CONFLICT (hour) DO UPDATE SET n = fo_budget.n + 1 WHERE fo_budget.n < ${BUDGET_PER_HOUR}
        RETURNING n
      ), add AS (
        INSERT INTO fo_counts (day, field, value, n)
        SELECT ${day}::date, t.f,
          CASE WHEN EXISTS (SELECT 1 FROM fo_counts c WHERE c.day = ${day}::date AND c.field = t.f AND c.value = t.v)
                 OR (SELECT count(*) FROM fo_counts c WHERE c.day = ${day}::date AND c.field = t.f AND c.value <> ${OVERFLOW}) < ${MAX_VALUES_PER_FIELD}
               THEN t.v ELSE ${OVERFLOW} END,
          1
        FROM unnest(${fields}::text[], ${values}::text[]) AS t(f, v)
        WHERE EXISTS (SELECT 1 FROM adm)
        ON CONFLICT (day, field, value) DO UPDATE SET n = fo_counts.n + 1
        RETURNING 1
      )
      SELECT (SELECT count(*) FROM adm)::int AS admitted`,
    ], { isolationLevel: "ReadCommitted" });
    admitted = results.at(-1)[0].admitted === 1;
  } catch {
    return new Response(null, { status: 503 });
  }
  if (!admitted) fullHour = hour;
  return new Response(null, { status: admitted ? 204 : 429 });
}

export function GET(request) {
  if (!exactPath(request, "/api/e")) return new Response(null, { status: 404 });
  return new Response("frontier-orchestration telemetry collector. POST only. See PRIVACY.md.\n",
    { status: 200, headers: { "content-type": "text/plain" } });
}
