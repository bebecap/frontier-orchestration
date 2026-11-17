# Privacy and telemetry

## Summary

- Telemetry is **opt-in**. Nothing is sent until you say yes in `fo.py setup` or run
  `fo.py telemetry on`.
- When on, only these commands send an event, at most one per run, with only the fields listed
  below: `install` and `setup` (both as `fo_setup`, after the config is saved), `doctor`,
  `providers`, `refresh`, `route`, `jev ping`, `lessons` and `ask`. `eval`, `config`,
  `telemetry`, `version`, every `--help`, and a command that stops on bad input before it starts
  its work send nothing.
- `DO_NOT_TRACK=1` always wins.

## When telemetry is off

Telemetry is off when **any** of these is true:

| Condition | How |
|---|---|
| you have not opted in | default |
| you opted out | `fo.py telemetry off`, or answered no in setup |
| `DO_NOT_TRACK=1` | the [Do Not Track](https://donottrack.sh/) convention |
| `FO_TELEMETRY=0` or `FO_TELEMETRY_DISABLED=1` | per-tool switch (an environment variable can only turn telemetry off, never on) |
| a CI environment | `CI`, `GITHUB_ACTIONS`, `GITLAB_CI`, `BUILDKITE`, `CIRCLECI`, `TRAVIS`, `TF_BUILD`, `JENKINS_URL`, `TEAMCITY_VERSION`, `BITBUCKET_BUILD_NUMBER`, `CODEBUILD_BUILD_ID` |
| this build has no collector | `data/telemetry.json` has no project key |

`FO_TELEMETRY_DEBUG=1` prints the exact event to stderr and sends nothing.

## What one event contains

The schema is an allowlist in `skills/frontier-orchestration/scripts/fo_telemetry.py`. Any other
key, and any value outside its list, is dropped before sending. A test fails if this changes.

| Field | Values |
|---|---|
| event | `fo_setup`, `fo_doctor`, `fo_providers`, `fo_refresh`, `fo_route`, `fo_jev_ping`, `fo_lessons`, `fo_ask` (the allowlist also accepts `fo_config` and `fo_telemetry`; no command of this version sends them) |
| `fo_version`, `python` | for example `0.1.0`, `3.12` |
| `os`, `arch` | `darwin`, `linux`, `windows`, `other`; `arm64`, `x86_64`, `other` |
| `host` | `claude-code`, `codex`, `agy`, `unknown`, `other` |
| `jev_backend`, `judge` | `typesafe`, `openjev`, `codiv`, `custom`, `off`; `jev`, `heuristic`, `none` |
| `via_claude`, `via_codex`, `via_gemini` | the transport kind: `host`, `cli`, `gemini-cli`, `agy`, `none` |
| `mode` | how setup ran: `interactive`, `flags` |
| `ask_vendor`, `ask_status` | for `fo_ask`: `claude`, `codex`, `gemini`; `ok`, `failed`, `timeout`, `empty`, `refused` |
| `subcommand` | for `fo_lessons`: `recall`, `add`, `reinforce`, `contradict`, `retire`, `run-new`, `run-close`, `lint`, `init`, or `none` (the allowlist also has config and telemetry subcommand names, which this version never sends) |
| `ok`, `independent_review_possible` | true or false |
| `tasks`, `acted`, `escalated`, `rejected`, `vendors_reachable` | small integers (the allowlist also has `lessons_shown`, which this version never sends) |
| `duration` | `lt1s`, `1to10s`, `10to60s`, `gt60s` |
| `error_kind` | `none`, `config`, `input`, `independence`, `jev_auth`, `jev_network`, `jev_other`, `ledger`, `other` |
| `distinct_id` | a new random UUID for every event, never reused (the PostHog format needs the field). It is sent with the event and kept in your local audit log; this project's collector ignores it and stores nothing derived from it |
| `timestamp` | UTC, to the second |
| fixed fields | always the same values: `schema` (the schema version, now 4), `$lib` = `frontier-orchestration`, `$geoip_disable` = true, `$process_person_profile` = false |

## What is never sent

Prompts, task text, model ids you route, model output, file paths, repository names or remotes,
user names, hostnames, IP-derived location, environment variables, error messages, stack
traces, API keys, any persistent identifier, or any hash of these.

## Where it goes

Events go by HTTPS to the project's own collector, `https://fo-telemetry.vercel.app/api/e`. Its
full source is in `collector/` of this repository. It runs as Vercel Functions in `fra1`
(Frankfurt) with a Neon Postgres database in the same region.

- The collector applies the same allowlist again (`collector/schema.json`; a test keeps it equal
  to the client's).
- **No event and no id is stored.** One database transaction admits the event and adds 1 to
  per-day counters: one for the event name and one for each allowlisted property value. There is
  no install id, so the project counts usage, not users (the Homebrew approach).
- Each field keeps at most 100 distinct values per day, plus one `(overflow)` row; a new value
  after that is counted under `(overflow)` instead of its own name. This holds for concurrent
  events too: the collector counts the values under a lock for that day and field.
- Kept: per-day counters (365 days) and a global count of admitted events per hour (about two
  days, for the write budget). PostgreSQL internals can show which counters one event changed
  until those rows are next updated; the project does not read them.
- It never stores the IP address or any header. The event endpoint reads only the `Content-Type`
  header, to refuse anything that is not `application/json`, and the `Content-Length` header, to
  refuse a body over 8192 bytes before it is read. Vercel's
  platform keeps its own short request logs, which include the IP, under Vercel's retention
  policy, and a Vercel Firewall rule limits each IP to 60 events per hour; this project reads
  neither.
- The hourly budget (2000 admitted events) stops counting: over the limit, or when the transaction
  fails, no counter changes and the event is dropped. (PostgreSQL may still touch the budget row
  internally, for example to lock it.) If the connection fails after the database committed, the
  collector cannot know and answers 503 although the counters changed. The command that sent the
  event is never affected.
- A daily job deletes counters older than 365 days and budget rows older than two days.
- The maintainer sees only the aggregate counts and the global per-hour budget use
  (`/api/stats`, token protected).
- The counters are not authenticated: the client's project key is public, so anyone can send
  events that pass the allowlist and inflate the counts, within the per-IP and hourly limits.
  Read every number as an approximate upper bound, not an exact count of real use.

The event format is PostHog-compatible, so a fork can point `FO_TELEMETRY_ENDPOINT` and
`FO_TELEMETRY_KEY` at its own collector or a PostHog project.

The sender runs as a detached process with a 3 second limit and no retry; a failure never changes
a command's result. Every event is first written to a local audit log,
`~/.local/state/frontier-orchestration/telemetry-log.jsonl` (mode 0600, last 500 events).
`fo.py telemetry log` prints it. There is no install id; files an earlier build kept
(`telemetry-id.json` and flag files) are deleted on the next run.

## Other data flows

- **Jev**: `fo.py route` sends the task attributes and model descriptions in your spec to the Jev
  backend you chose. With `openjev` on your own machine, nothing leaves it.
- **Vendors**: reviews and delegations go to the vendors you enabled, under their own terms.
- **Ledger**: lessons and runs stay in a local folder you choose.

Your consent is recorded with the schema version (now 4). When a release changes the event fields,
the version goes up and telemetry stays off until you opt in again (`fo.py telemetry on`).
