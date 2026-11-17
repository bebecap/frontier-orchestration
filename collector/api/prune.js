// GET /api/prune : daily cron. Deletes day counters older than 365 days and hourly budget rows
// older than 2 days.
import { db } from "./_db.js";
import { timingSafeEqual } from "node:crypto";
import { exactPath } from "./_path.js";

export function authorized(request, secret) {
  const got = Buffer.from(request.headers.get("authorization") || "");
  const want = Buffer.from(`Bearer ${secret || ""}`);
  return Boolean(secret) && got.length === want.length && timingSafeEqual(got, want);
}

export async function GET(request) {
  if (!exactPath(request, "/api/prune")) return new Response(null, { status: 404 });
  if (!authorized(request, process.env.CRON_SECRET)) return new Response(null, { status: 401 });
  const sql = db();
  await sql`DELETE FROM fo_counts WHERE day <= (now() AT TIME ZONE 'utc')::date - 365`;
  await sql`DELETE FROM fo_budget WHERE hour < to_char((now() AT TIME ZONE 'utc') - interval '2 days', 'YYYY-MM-DD"T"HH24')`;
  return Response.json({ ok: true });
}
