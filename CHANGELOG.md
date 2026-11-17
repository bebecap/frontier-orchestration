# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-17

First public release.

### Security

- Release gate: `--allow-email ADDRESS` (repeatable) lets the owner publish under one chosen
  address. It allows that address only as an author, committer or tagger email, as an exact
  match that ignores ASCII case only. A non-ASCII identity email never matches, so a look-alike
  such as the Kelvin sign, which `str.lower()` maps to `k`, is not folded into a match. A value
  with a wildcard, whitespace or more than one `@` is refused (exit 2). The address in a message
  or a file still fails. Without the option the rule is unchanged.
- Release gate: the checker now has two layers, and only the first is keyed by a blob id.
  `blob_facts` holds what the bytes alone show (content findings, NUL bytes, format by magic
  bytes, archive magic, LFS pointer shape, embedded base64 payload) and is computed once per blob.
  `entry_findings` holds every rule that reads a path, an extension or a mode (review list,
  embedded data, archive by name, `--allow-archive`, LFS pointer and archive only for a regular
  file, submodule, deny list file, `ALLOW`, path findings) and runs for every tree entry of every
  commit. Before, the review reason and the content findings were computed only for the first
  path that held a blob, so `fixtures/example.txt` hid an identical `preview.html` with embedded
  base64 data, and a second file with the same secret was not named. Each line is now printed
  once per path, at the first commit that has it. A table test checks, for every path or mode
  rule, that an entry that shares its bytes gets the result it gets alone. New tests also cover
  every listed format and archive name or magic, remote-tracking branch names, refs through a
  tag, Greek look-alikes, and that a committed deny list's content is not scanned.
- Release gate: the "binary files, review by eye" list no longer depends on NUL bytes. A file is
  listed, with the reason, when its magic bytes or its extension show a format the gate cannot
  read as text (PDF, PostScript, images, fonts, audio, video, SQLite, pickle, compiled objects),
  and an .svg, .html or .md file with a `data:...;base64,` payload of 200 characters or more is
  listed as embedded data. Before, a PDF with compressed text had no NUL bytes, so it had no
  finding and was not listed. Office files are zip archives and fail closed (test added). Listed
  files still do not fail the gate. The shipped-file privacy test uses the same rule.
- Collector: `POST /api/e` accepts only `Content-Type: application/json` (415 otherwise). A web
  page could before make every visitor's browser send a forged event from its own IP with a
  cross-site `text/plain` POST, which needs no CORS preflight. Every client version sends
  `application/json`.
- Collector: each (day, field) keeps at most 100 distinct values, plus one `(overflow)` row; a new
  value after that is counted under `(overflow)`. Before, made-up `fo_version`, `python` or integer
  values added a new row for each event, so the table could grow without limit. The limit holds
  under concurrency: the event's transaction takes an advisory lock per (day, field), in sorted
  order, before a READ COMMITTED count. Before this, 32 concurrent events at 99 values could all
  add a row. A real-PostgreSQL test sends 64 concurrent new values and gets exactly 100 plus
  `(overflow)`; another shows the hourly budget admits exactly the rest of the hour under 64
  concurrent events.
- Release gate (`scripts/prepublish_check.py`): it checks every path of every reachable commit
  (both sides of a rename, deleted files), not one path per distinct content; it scans content as
  bytes (UTF-8, Latin-1, a declared commit encoding, UTF-16/32 for files with NUL bytes or a BOM),
  so one byte that is not UTF-8 no longer skips a whole file; and it lists binary files for review
  by eye. It also checks tag names, notes refs, the tagger of a merged tag (mergetag), upper-case
  email addresses, and deny-listed words in any case or Unicode variant. It fails on a submodule,
  a Git LFS pointer and an identity line it cannot read, and stops (exit 2) on a shallow clone, a
  grafts file or a missing object. It ignores replace refs and never fetches a missing object.
  The email pattern no longer takes quadratic time on a long run of letters: before, a 3 MB text
  file kept the gate busy for more than 60 seconds. The shipped-file privacy test uses the same
  byte scanner and fails on a file it cannot read.
- `tests/e2e/smoke.sh` runs under `tests/e2e/supervise.py`, which owns a loopback 503 server for
  the whole run, points the installer's OpenJev check and telemetry at it, and removes inherited
  `FO_*` and proxy variables. Before, the installer step could contact a real OpenJev on
  127.0.0.1:8080. The entry point now always starts the supervisor: before, a parent shell that
  exported `FO_SMOKE_SUPERVISOR_PID=$$` and its own `FO_OPENJEV_URL` skipped supervision, and the
  run sent its decision-engine request to that server. The supervisor runs
  `tests/e2e/smoke_body.sh` from a fixed path; the body refuses to run unless the server at
  `FO_OPENJEV_URL` echoes a one-time token that the supervisor gave it on a pipe, and it removes
  `FO_*` and proxy variables and sets the engine and telemetry off itself.
- Release gate: rules that depend on a tree entry are checked for every entry. Before, when a
  symlink and a regular file had the same bytes, the first one decided for both, so a symlink
  could hide a Git LFS pointer in a regular file.
- Release gate: the name of every ref it walks (branches, tags, notes refs, remote-tracking
  branches and other refs that point into the history) is scanned and reported, with the name
  withheld. Before, a sensitive notes ref name only changed the label and was never reported, and
  branch names were not checked.
- Release gate: deny-listed words are compared after every mark (Mn, Mc, Me), format character and
  default-ignorable code point is removed. Before, a mark of combining class 0 (U+034F, U+FE0F, a
  variation selector supplement character, a spacing mark), an enclosing mark or a Hangul filler
  inside a word hid it.
- Release gate: an archive (`.zip`, `.gz`, `.tar`, `.tgz`, `.7z` and others, by name or by its
  first bytes) fails the gate until you pass its path with `--allow-archive`, because the gate
  cannot read compressed content. A partial clone stops the gate (exit 2). CONTRIBUTING.md now
  lists the known limits: known patterns only, the ALLOW files, compressed content, and one ref.
- Plain HTTP and loopback requests never go through a proxy. Before, with `http_proxy` or
  `all_proxy` set, a request to a local OpenJev at `http://127.0.0.1:8080` passed the transport
  check but sent the prompt (and any key) through the remote proxy in plaintext. This covers the
  engine client, the installer's OpenJev check and the telemetry sender. HTTPS to another host
  still uses the proxy from the environment.
- The installer's OpenJev check refuses redirects, like the engine client. Before, an
  `FO_OPENJEV_URL` on HTTPS could redirect the check to plain HTTP on another host.
- An engine URL with a user name or password, bad brackets or a bad port is refused with a clear
  message. A malformed `FO_OPENJEV_URL` (for example `http://[`) is ignored with a warning;
  before, it crashed the installer.
- Collector: every function answers only on its exact path (`/api/e`, `/api/stats`,
  `/api/prune`); aliases such as `/api/e/` or `/api/e.js` are 404, so a firewall rule on the exact
  path covers every request.
- Collector: `POST /api/e` refuses a `Content-Length` over 8192 with 413 before it reads the
  body. After a budget 429, an instance answers 429 for the rest of that UTC hour without a
  database call (never after a 503). Every function has `maxDuration` 10 s.

### Changed

- Path settings (`ledger.path`, `ledger.log`, `FO_LEDGER`, `FO_LEDGER_LOG`, `FO_VAULT`, the
  installer answer, `setup --ledger`, `config set`) have one normal form: spaces around the value
  are removed before it is checked, saved and used. Before, `" ~/notes"` passed the check and then
  made a folder under the current folder.
- `fo.py config set` takes several `KEY VALUE` pairs and checks the result once, so two keys that
  are only valid together (a relative `ledger.path` and `ledger.log`) can be fixed in one call.
- Bad input never shows a Python traceback. `route` prints `route failed: cannot read <path>:
  <reason>` for a missing file, bad JSON, non-UTF-8 text or a JSON list. A config file with a
  section of the wrong shape is a `config error`. `ask` checks `--cwd`, `--prompt-file` and
  `--timeout` before it starts the vendor CLI. Any other error prints one line; `FO_DEBUG=1`
  shows the traceback.
- Hints from the installer, `setup`, `doctor` and the telemetry status print the full runnable
  command (`python3 -I <path>/fo.py ...`). The docs define `fo.py` as that command.
- `doctor` before any setup says `setup has not run yet` and names the command, instead of a bare
  key failure. It still exits 1.
- `providers` and `doctor --live` use one sign-in check: Claude reached through the host is shown
  as `host session, assumed signed in` in both.
- `fo.py lessons <subcommand> --help` shows that subcommand's own flags.
- `eval` shows no host when the engine is off.
- A missing engine key names the ways that work on this platform (on Linux: `fo.py install`, a key
  file with mode 600 and `jev.api_key_file`, or the environment variable). The Python 3.10 hint
  shows uv first, then Homebrew on macOS or the distribution's package manager on Linux. The
  README says that self-hosted OpenJev on Linux needs an NVIDIA GPU and Docker.
- Collector: `GET /api/stats` adds `budget` (admitted events per UTC hour for the last 48 hours,
  the limit, and saturated hours); the dashboard shows a "Write budget (last 48 h)" section.
- PRIVACY.md lists exactly the commands and events that send telemetry, and says that the
  counters are unauthenticated, so the numbers are approximate upper bounds.
- CI runs every test job on each push and pull request (no repository-variable gate), plus a Linux
  end-to-end smoke run (`tests/e2e/smoke.sh`), the collector tests and the installer prototype
  tests on Node 22.
- Tests: the OpenJev check and the telemetry sink go to a loopback server that the test process
  owns for its whole run, not to port 9.

- `fo.py route` now labels a REJECT as `REJECTED (pick is not an allowed candidate)`. Before, the
  label named the independence rule, but a REJECT also happens on a task with no independence
  rule (a pick outside `candidates`, or from a vendor that is not enabled). The `fo.py --help`
  exit-code line says the same.
- README follows the common skill-repository layout: install per host, first run, measured
  results with their limits, update and uninstall. A social preview image is in `docs/assets/`.
- The `SKILL.md` description is in the third person.

### Added

- `scripts/prepublish_check.py`: a release gate that reads the whole git history. It fails on an
  author, committer or tagger email that is not a noreply address, a `Claude-Session` trailer, or
  a deny-listed word or secret pattern in any commit message or any file version, and on a
  committed `.privacy-denylist`. A history that fails must not be published; publish a
  single-commit snapshot. `SECRET_PATTERNS` and `ALLOW` now live in it.

- `npm/`: a one-command installer prototype (`"private": true`, not published). It copies the
  plugin to a stable folder and registers it with Claude Code, Codex, Antigravity and the shared
  skills folder. It records each host step, runs one install or uninstall at a time (lock file),
  and deletes only folders that carry its ownership marker.
- `docs/` guides (install, how it works, cost, eval, providers, telemetry, troubleshooting) and
  `llms.txt`, a map of the repository for LLMs.
- CI on GitHub-hosted macOS and Ubuntu runners, with a gate job that fails the run unless the
  test job passed.
- `AGENTS.md`, issue templates and a pull request template.
- `CONTRIBUTING.md` states exactly what the tests mock and what runs against real local servers.
- The version test also checks `npm/package.json`.
- `FO_OPENJEV_URL`: where `fo.py install` looks for OpenJev on this machine (default
  `http://127.0.0.1:8080`). `https://`, or `http://` to `127.0.0.1` or `localhost`; another value
  is named and ignored.

### Fixed

- `fo.py <command> -h` and `fo.py <command> --help` print the usage of that command and exit 0.
  The command does no work: no config write, no network request, no vendor CLI, no telemetry
  event. Before, `refresh --help` did a refresh, `install --help` started the installer and
  `setup --help` showed the setup questions. A `-h` or `--help` after `--` is data.
- A ledger path must be a full path (it starts with `/` or `~`). The installer refuses a relative
  answer before it creates anything; `ledger.path`, `ledger.log`, `FO_LEDGER`, `FO_LEDGER_LOG`
  and `FO_VAULT` with a relative path are a config error, and `fo.py config set` does not write
  one. Before, the answer `2` made a folder `2/` in the current folder.
- The test suite no longer depends on what listens on this machine. With a real OpenJev on
  127.0.0.1:8080 the installer skipped a question, so the scripted answers shifted. Each test now
  points the installer's OpenJev check at a closed port and runs in its own temporary folder;
  the npm tests run their CLI from the sandbox home.
- Docs: the compact OpenJev build's memory, measured with each MLX buffer cap, and its current
  request times (the earlier 1.0 s sanity figure was from the first build comparison).

### Packaging (2026-11-15)

- Telemetry is live: the project's own collector (`collector/`, Vercel Functions and Neon
  Postgres in fra1) re-applies the allowlist and stores only per-day aggregate counters: no event
  rows, no ids, no IP, no headers. The client has no install id: each event carries a fresh
  random UUID, so events cannot be linked (usage is counted, not users). One SQL statement admits against an hourly budget and writes the counters, or writes
  nothing. A per-IP firewall limit also applies. Counters older than 365 days are deleted daily.
  Still opt-in. Consent is recorded per telemetry schema version (now 4); a field change needs a
  new opt-in.
- One repository serves as the plugin and the marketplace for Claude Code, OpenAI Codex and
  Antigravity, and installs through skills.sh for other agents.
- `fo.py`: one stdlib command line for setup, doctor, providers, refresh, route, the ledger and
  telemetry.
- Jev backends: TypeSafe Jev, OpenJev (self-hosted), OpenJev on Codiv, any compatible server,
  or off (a local rule-based router).
- Interactive installer (`fo.py install`, Clack-style terminal UI, stdlib only): decision engine
  (TypeSafe key from Keychain or environment, OpenJev install and start, Codiv, none), providers
  (Claude, Codex, Antigravity; one or more, at least one signed in), telemetry consent, ledger.
- Providers through their own CLIs only: `fo.py ask` runs one read-only ask (argv, no shell,
  hard timeout, stale answers removed). No bridge, MCP server or other daemon.
- Installer review round 1 (Sol and Astra, through `fo.py ask`): a new engine choice starts a
  fresh `jev` section, so a key is never checked against the previous engine's server; the
  chosen key source (`jev.key_source`: env, keychain or file) is obeyed; each ask gets its own
  private answer file (0600 in a 0700 folder); a timeout or Ctrl-C always stops the CLI's whole
  process group; an interrupted OpenJev install can be finished; the launcher quotes its path;
  old `bridge` / `codex-plugin` settings fall back to `auto`; a gemini CLI OAuth sign-in counts;
  a lone Escape cancels; `fo.py providers` shows sign-in state.
- Installer review rounds 2-5: one rule for engine settings (kept only when the stored and the
  effective engine both match the choice; an engine picked by `FO_JEV_BACKEND` never inherits
  another engine's URL or key settings); signals held while the CLI starts, default handlers in
  the child; empty placeholders removed on every interruption; atomic private key files; the
  ledger folder checked before the config is saved; gemini OAuth files validated.
- Opt-in anonymous telemetry with an allowlist, `DO_NOT_TRACK`, CI detection, debug mode and a
  local audit log.
- The learning ledger works in any folder; `FO_VAULT` keeps an Obsidian vault layout working.
- `references/rules.md`: 61 confirmed rules distilled from the maintainer's ledger.
- Hardening from a blind cross-vendor review: the Jev client redacts the key from errors and
  refuses redirects; the telemetry sender checks the exact loopback host and refuses redirects;
  the audit log is locked; `needs_independence` accepts booleans and fails closed on anything
  else; a forced but missing transport is not reachable; refresh keeps agy's Keychain guard unless
  `refresh.agy_keychain_fallback` is on; the privacy notice ships inside the skill.
- Round 2: the audit log lock waits at most 0.5 s and an unaudited event is never sent; the key
  is redacted before an error is shortened (also a key cut at the read limit);
  `needs_independence` accepts only yes/no/true/false/y/n with an optional note; ledger set-up
  runs under the folder lock; `config set providers.<vendor>.model` works (the `command` key was later removed).
- Round 3: Jev errors never show the server's response body (status code and a local hint only);
  a judge number that is not a finite probability in [0, 1] stops the route; a ledger log shared
  by several ledgers is locked on the log itself; the telemetry sender has a wall-clock deadline.
- Round 4: judge values, rejected picks and protocol errors no longer echo server text; Jev requests use HTTPS, or HTTP only to loopback unless
  `jev.allow_insecure_http`; the ledger log lock follows symlinks to the real file.
- Round 5 (both reviewers approved; these close pre-existing items): `jev ping` prints the
  configured model, never the server's; a judge (Jev) never counts as an independent reviewer;
  Jev replies have an 8 MB cap.
- Round 6 (both reviewers approved 4dc7c7d; these close pre-existing items): the Jev request runs
  in a worker thread with a hard wall-clock limit covering connect, headers, chunk framing and
  body; a huge integer probability is a clean route error; ledger free text that starts with '#'
  is escaped, so it cannot forge a section heading.
- Round 7 (both reviewers approved 1d82815): `jev.timeout_s` must be a number of seconds
  (0, 3600]; `doctor` probes the ledger with a fresh temporary file, never a fixed name.
- `fo.py eval`: an eval harness for the decision engine. A sanity suite (24 System One
  questions, malformed answers are errors) and a routing suite (20 labeled tasks: pick and
  stakes accuracy, Brier score, no REJECT, independence kept), with `--repeat` stability,
  latency p50/p95 and exit codes 0/1/2. `--backend` never carries the stored engine's
  settings to another engine. No telemetry. Heuristic baseline: pick accuracy 0.60, stakes accuracy 0.778, Brier 0.132.
- OpenJev on this machine, tested end to end on a clean Mac (system Python 3.9): the installer
  finds a Python 3.10+ in the usual places (or offers `uv python install 3.12`), checks out the
  OpenJev commit it was tested with (b4339c64, 0.7.2), and lets you pick a DiffusionGemma MLX
  build pinned to an exact Hugging Face revision: Compact (mxfp4, about 14 GB, the default) or
  Standard (4-bit, about 16 GB), or any full DiffusionGemma MLX build. A running server is
  configured with the model it serves.
- Telemetry: test runs can no longer reach the live collector (a loopback sink in the test
  helpers); the 131 counters our own tests had written were removed. The collector serves a
  maintainer dashboard (`https://fo-telemetry.vercel.app/`, stats token kept in the tab's
  session storage only, strict CSP) and no longer serves its source files.
- `tests/e2e/`: an installer driver that answers prompts by their text, and scripts that make
  and compare OpenJev MLX builds with `fo.py eval`.
- `providers.<vendor>.command` was removed. It replaced the read-only command line, and two
  review rounds kept finding ways a custom command could stay writable. `fo.py ask` always runs
  its built-in read-only command; an old config with the key loads with it ignored, and
  `fo.py doctor` says to delete it. A CLI that is not on `PATH` goes in `FO_PATH_EXTRA`.
- The OpenJev launcher caps MLX's buffer pool at 1 GB for the pinned builds (peak memory 19 GB
  instead of 22, same eval results).

[Unreleased]: https://github.com/bebecap/frontier-orchestration/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/bebecap/frontier-orchestration/releases/tag/v0.1.0
