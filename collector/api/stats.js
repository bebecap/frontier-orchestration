// GET /api/stats?days=30 : aggregate counts for the maintainer (Bearer STATS_TOKEN).
// Also "series" (events per day, for the dashboard chart), "fields" (the field names present)
// and "budget" (admitted events per UTC hour for the last 48 hours, against the hourly limit).
import { db } from "./_db.js";
import { authorized } from "./prune.js";
import { exactPath } from "./_path.js";
import { BUDGET_PER_HOUR } from "./_budget.js";

// The answer holds private aggregates: no browser or proxy may keep a copy, not even of a 401.
const NO_STORE = { "cache-control": "no-store" };

export async function GET(request) {
  if (!exactPath(request, "/api/stats")) return new Response(null, { status: 404, headers: NO_STORE });
  if (!authorized(request, process.env.STATS_TOKEN)) return new Response(null, { status: 401, headers: NO_STORE });
  const days = Math.min(Math.max(parseInt(new URL(request.url).searchParams.get("days") || "30", 10) || 30, 1), 365);
  let rows, perDay, perHour;
  try {
    const sql = db();
    [rows, perDay, perHour] = await Promise.all([
      sql`SELECT field, value, sum(n)::int AS n FROM fo_counts
        WHERE day > (now() AT TIME ZONE 'utc')::date - ${days}::int GROUP BY field, value ORDER BY field, n DESC`,
      // to_char: the driver would turn a date column into a JS Date in the server's time zone.
      sql`SELECT to_char(day, 'YYYY-MM-DD') AS day, value, sum(n)::int AS n FROM fo_counts
        WHERE field = 'event' AND day > (now() AT TIME ZONE 'utc')::date - ${days}::int
        GROUP BY day, value ORDER BY day, value`,
      // This UTC hour and the 47 before it. prune.js deletes budget rows after 2 days.
      sql`SELECT hour, n FROM fo_budget
        WHERE hour >= to_char((now() AT TIME ZONE 'utc') - interval '47 hours', 'YYYY-MM-DD"T"HH24')
        ORDER BY hour`,
    ]);
  } catch {
    return new Response(null, { status: 503, headers: NO_STORE });
  }
  const counts = Object.create(null);
  for (const r of rows) { (counts[r.field] ??= Object.create(null))[r.value] = r.n; }
  const series = [];
  for (const r of perDay) {
    if (series.at(-1)?.day !== r.day) series.push({ day: r.day, counts: Object.create(null) });
    series.at(-1).counts[r.value] = r.n;
  }
  const budget = { per_hour: BUDGET_PER_HOUR,
    hours: perHour.map((r) => ({ hour: r.hour, n: r.n, saturated: r.n >= BUDGET_PER_HOUR })) };
  return Response.json({ days, events: Object.values(counts.event || {}).reduce((a, b) => a + b, 0), counts,
    series, fields: Object.keys(counts), budget }, { headers: NO_STORE });
}
