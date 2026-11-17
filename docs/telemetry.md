# Telemetry

> `fo.py` on this page is short for `python3 -I <skill folder>/scripts/fo.py`. See
> the top of [install.md](install.md) for the skill folder.

Telemetry is **opt-in**. Nothing is sent until you say yes in setup, or run
`fo.py telemetry on`. Your agent must not answer the setup question for you.

## What it sends

When telemetry is on, each command sends one anonymous event. The event holds only fields from
a fixed allowlist: for example the command, the skill version, the OS, the Python version, the
engine kind, small counts and the outcome. There is no install id, so events cannot be linked.

It never sends prompts, task text, file paths, repository names, model output, error text, host
names, user names or keys. `fo.py eval` sends no telemetry.

## Where it goes

Events go to the project's own collector. The collector keeps only daily counters. It stores no
event, no id and no IP address. Counters older than 365 days are deleted. The collector source
is in [`collector/`](../collector/).

The maintainer reads the counters on a dashboard at <https://fo-telemetry.vercel.app/>. It needs
a stats token.

## Commands

```bash
fo.py telemetry status     # on or off, and why
fo.py telemetry on         # opt in
fo.py telemetry off        # opt out
fo.py telemetry log        # the last 20 events this machine sent
fo.py telemetry log 100    # the last 100 (the local log keeps the newest 500)
```

Telemetry is also off when `DO_NOT_TRACK=1`, `FO_TELEMETRY=0` or `FO_TELEMETRY_DISABLED=1` is
set, or in a CI environment. No environment variable can turn it on. `FO_TELEMETRY_DEBUG=1`
prints the event and sends nothing.

The full field list, the retention rules and the other data flows are in
[PRIVACY.md](../PRIVACY.md).

Next: [troubleshooting.md](troubleshooting.md).
