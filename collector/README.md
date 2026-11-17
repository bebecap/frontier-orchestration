# Telemetry collector

The server side of frontier-orchestration's opt-in telemetry (see `../PRIVACY.md`). Vercel
Functions in `fra1` (Frankfurt) with a Neon Postgres database in the same region.

- `POST /api/e`: one event. The allowlist in `schema.json` (the same one the client uses) is
  applied again. No event and no id is stored: one transaction of two SQL statements, sent in one
  round trip, admits the event against the hourly budget and, only if admitted, adds 1 to per-day
  counters for the event name and each allowlisted property value. There is no install id. It
  reads only the `Content-Type` and
  `Content-Length` headers and stores no header and no IP. Anything but `application/json` is a
  415, so a web page cannot make a visitor's browser send an event without a CORS preflight,
  which this endpoint never allows. A `Content-Length` over 8192 is a 413 before the body is
  read; the body size is checked again after reading, because the header can be missing or
  wrong. Order of checks: path (404), content type (415), `Content-Length` (413), body size
  (413), JSON and allowlist (400), hour full (429), database.
- Exact paths only: Vercel also sends `/api/e/`, `/api/e.js`, `/api/e/x` and the like to the same
  function, and a Firewall rule that matches the exact path does not cover them. So every handler
  (`/api/e` POST and GET, `/api/stats`, `/api/prune`) answers 404 with an empty body on any path
  that is not exactly its own (the query string does not matter; case matters). This check runs
  first, before any header, auth or body check (`api/_path.js`).
- Distinct values: each (day, field) keeps at most 100 distinct values, plus one `(overflow)` row.
  A new value after that is counted under `(overflow)`, so made-up versions or integers cannot
  grow the table without limit. The limit holds for concurrent requests too: the first statement
  takes a transaction-level advisory lock for each (day, field) of the event, in sorted order, and
  the second statement counts the values under those locks in a new READ COMMITTED snapshot. A
  real-PostgreSQL test sends 64 concurrent new values to a field that has 99 and gets exactly 100
  values plus `(overflow)`.
- Admission: a conditional atomic hourly counter in Postgres (`FO_BUDGET_PER_HOUR` when it is a
  positive integer, else 2000; `api/_budget.js`). It is `INSERT ... ON CONFLICT DO UPDATE ...
  WHERE n < limit`, which locks the hour's row and checks its latest value, so concurrent events
  cannot pass the limit: a real-PostgreSQL test sends 64 concurrent events with 10 left and gets
  exactly 10 admitted. Over budget (429) or on a statement error (503) no counter changes (fail
  closed). A connection lost after commit returns 503 although the counters changed. A Vercel
  Firewall rule (project setting, not in this code) also allows 60
  events per IP per hour on `/api/e`.
- Hour full: after the database answers "over budget" for a UTC hour, that function instance
  answers 429 for the rest of the hour with no database call. Only a budget 429 sets this, never a
  503. It is kept in memory per instance: a new instance asks the database once, and a new hour
  asks again.
- Time limit: `vercel.json` sets `maxDuration` 10 seconds for every function in `api/`.
- `GET /api/prune`: daily cron (Bearer `CRON_SECRET`); deletes day counters older than 365 days and
  hourly budget rows older than 2 days.
- `GET /api/stats?days=30`: aggregate counts (Bearer `STATS_TOKEN`). The answer has `days`,
  `events`, `counts` (`{field: {value: n}}`), `series` (events per UTC day:
  `[{day: "YYYY-MM-DD", counts: {event: n}}]`, oldest first, only days with events) and `fields`
  (the field names in `counts`), and `budget`: `{per_hour, hours: [{hour: "YYYY-MM-DDTHH", n,
  saturated}]}`, the admitted events per UTC hour for this hour and the 47 before it, oldest first,
  only hours with a row. `per_hour` is the same limit `/api/e` uses; `saturated` is `n >= per_hour`.
  `budget` does not depend on `days`. `days` is 1 to 365. Every answer, also a 401 and a 404,
  has `Cache-Control: no-store`.
- `/` (`public/`): the maintainer dashboard. It is a static page that calls `/api/stats`.

Tables: `schema.sql`. Environment: `DATABASE_URL`, `FO_PUBLIC_KEY` (the public key the client
sends; it only allows adding to counters), `CRON_SECRET`, `STATS_TOKEN`.

## Dashboard

Open https://fo-telemetry.vercel.app/ and enter the stats token. The token is in the maintainer's
macOS Keychain, item `FO_TELEMETRY_STATS_TOKEN`. To copy it to the clipboard without showing it:

```sh
security find-generic-password -s FO_TELEMETRY_STATS_TOKEN -w | tr -d '\n' | pbcopy
```

Do not print the token in a terminal, a log, a chat or a ticket.

- The page keeps the token in `sessionStorage`, for this tab session only. A browser can restore
  `sessionStorage` when it restores a closed tab, so closing the tab is not enough: use "Forget
  token" on a shared machine. A 401 ("Token rejected") also removes it. The token is never in the
  URL or in `localStorage`.
- The token must be visible ASCII (`!` to `~`, no spaces). The page does not store or send any
  other token, and shows "Token has invalid characters".
- The token field is `type="password"` with `autocomplete="one-time-code"`, so most browser password managers
  do not offer to save it. The field is disabled while a request runs.
- It shows the total events, events by type, a chart of events per day (7, 30 or 90 days), the
  write budget (last 48 h: events per UTC hour and the share of the hourly limit; an hour at the
  limit has a "saturated" label), and a table for each field with counts and share.
- It loads no code, style or font from another site. `vercel.json` sends a strict
  Content-Security-Policy and other security headers on every path, so the page runs only its own
  `dashboard.js` and `dashboard.css`. Field values are shown as text only.
- `outputDirectory` is `public`, so Vercel serves only the dashboard files as static files, not
  the rest of this folder.

## Tests

Node 22.3 or later. No network and no `npm install`: a stub replaces `api/_db.js`. The
real-PostgreSQL tests in `test/e.test.js` run the SQL on a throwaway local cluster when `initdb`,
`pg_ctl` and `psql` are on PATH; without them they are skipped, and say so.

```sh
cd collector && node --test --experimental-test-module-mocks
```

Vercel's platform keeps its own short request logs, which include the client IP, under Vercel's
retention policy. This code does not read them.

## Deploy

Production is the Vercel project `fo-telemetry`: run `vercel deploy --prod` from `collector/`.

Deploy your own: `vercel link`, add a Postgres database (for example `vercel integration add
neon`), run `schema.sql`, add the variables, `vercel deploy --prod`, then point
`FO_TELEMETRY_ENDPOINT` and `FO_TELEMETRY_KEY` at it.
