# Troubleshooting

> `fo.py` on this page is short for `python3 -I <skill folder>/scripts/fo.py`. See
> the top of [install.md](install.md) for the skill folder.

Start with `fo.py doctor`. Add `--live` to also make one tiny engine call and check that each
provider CLI is signed in.

## Read the doctor output

Each line starts with a word:

| Start | Meaning |
|---|---|
| `ok` | this check passed |
| `note` | not an error, but read it |
| `FAIL` | this check failed. `doctor` exits 1 |

`doctor` checks these items, in this order: the config file, the Python version, the roster,
the engine (URL, model, key), each provider, the ledger folder, telemetry, and the model
snapshot.

Example of a healthy machine, from a Claude Code host on 2026-11-16 (home paths shortened to
`~`, the ledger at its default folder):

```
ok   config ~/.config/frontier-orchestration/config.json
ok   python 3.14.7
ok   roster 17 models, checked 2026-11-15
ok   jev typesafe https://api.typesafe.ai model jev-latest; key found
host: claude-code
  Claude                 host        model: the CLI's default
      how: Agent tool (model alias fable | opus | sonnet | haiku). Same vendor as you: never the independent review.
  Codex                  cli         model: the CLI's default
      how: fo.py ask codex    (codex exec --sandbox read-only)
  Antigravity (Gemini)   agy         model: the CLI's default
      how: fo.py ask gemini   (agy --print --mode plan)
independent review possible: yes (vendors reachable: claude, codex, gemini)
ok   ledger ~/.local/share/frontier-orchestration/ledger
ok   telemetry: not opted in (fo.py telemetry on to opt in)
ok   model snapshot 0.1 h old, 1 drift item(s)
```

## Common failures

### A provider is not signed in

`doctor --live` prints:

```
FAIL codex: not signed in (not signed in); sign in: codex login
```

Sign in with the command it shows. See [providers.md](providers.md#sign-in). A CLI that is not
on `PATH` or in the default folders is not found: add its folder to `FO_PATH_EXTRA`.
`FO_PATH_EXTRA` replaces the default search list. After `PATH`, the skill searches
`~/.local/bin`, `/opt/homebrew/bin` and `/usr/local/bin`. When you set `FO_PATH_EXTRA`, those
folders are no longer searched. Include them in the value if you need them, for example
`FO_PATH_EXTRA="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/my/folder"`.

### The engine key is missing

`doctor` prints `FAIL jev: no API key for Jev backend 'typesafe'. Store it once in one of these
ways ...`, with one full command for each way that works on this machine. On macOS:

```bash
security add-generic-password -U -a "$USER" -s TYPESAFE_API_KEY -w
```

On Linux: `fo.py install` and "Add a new key" (a key file with mode `0600`), or your own key file
with `fo.py config set jev.api_key_file /path/to/the/file jev.key_source file`, or
`export TYPESAFE_API_KEY=...` in your shell profile. For Codiv the name is `CODIV_API_KEY`.
Never paste a key into a chat.

### The engine refuses the key

`HTTP 401` means the key was refused. `HTTP 403` means the key has no access to this model or
endpoint. Check that the key belongs to the engine in your config: `fo.py config show`.

### OpenJev does not answer

`cannot reach http://127.0.0.1:8080 or read its reply` means no server answers there.

1. Start it: `~/.local/share/frontier-orchestration/openjev/start-openjev.sh`.
2. Wait. The first start loads the weights into memory. This takes a minute or two.
3. Read `server.log` in the same folder.
4. Check again: `fo.py jev ping`.

### OpenJev is slow

`... took longer than 180 s to answer` means the request hit the time limit. The usual cause is
memory pressure:

1. Open Activity Monitor > Memory. If memory pressure is yellow or red, the system swaps (it
   moves memory to disk), and each decision gets much slower.
2. Close large apps, then restart OpenJev.
3. Keep the default 1 GB buffer cap. `OPENJEV_MLX_CACHE_LIMIT_GB` changes it.
4. Plan for a Mac with 24 GB of memory or more. After an eval, the compact build used about
   19 GB at its peak with the default 1 GB cap, and about 22 GB with a 4 GB cap. Look at
   `start-openjev.sh`: a launcher written before 2026-11-16 uses 4 GB.
5. If memory stays tight, switch to an engine that downloads nothing:
   `fo.py setup --jev typesafe`, `fo.py setup --jev codiv`, or `fo.py setup --jev off`.

To give a slow server more time, set `jev.timeout_s` (seconds, up to 3600).

More: [install.md](install.md#memory-pressure).

### Only one vendor is reachable

`note only one vendor is reachable: the different-vendor review rule cannot be met`. Install and
sign in to a second provider, or say plainly that the review is not independent.

### No model snapshot

`note no model snapshot yet: run fo.py refresh --wait`. Run that command.

### The ledger is not writable

`FAIL ledger <path> is not writable`. Choose another folder:
`fo.py config set ledger.path <folder>`.

### The ledger path must be a full path

`config error: ledger.path must be a full path that starts with / or ~` (or `ledger.log`, or
`FO_VAULT`). A relative path would depend on the folder you run the command from. Give a path
that starts with `/` or `~`, for example `fo.py config set ledger.path ~/notes/fo-ledger`, or
fix `FO_LEDGER`, `FO_LEDGER_LOG` or `FO_VAULT` in your shell.

When both `ledger.path` and `ledger.log` are relative, a `config set` of one key fails on the
other one (each `config set` checks the whole config). Fix both in one call:

```bash
fo.py config set ledger.path ~/notes/fo-ledger ledger.log null
```

`null` removes `ledger.log`, so the log goes to `log.md` in the ledger folder. Or give it a full
path: `ledger.log ~/notes/fo-ledger/log.md`.

### Doctor says setup has not run

`FAIL setup has not run yet (no config at ...)`. Run `fo.py install` (a person) or `fo.py setup`
(an agent) first. `doctor` exits 1 until a config exists, because nothing is chosen yet. It does
not check an engine key before setup.

### Bad input

`fo.py` prints one line and exits 1 for bad input, for example
`route failed: cannot read tasks.json: not valid JSON (...)`, or
`config error: ... must be a JSON object` for a config file of the wrong shape. An unexpected
error prints `fo.py failed: <error> (set FO_DEBUG=1 to see the traceback; please report it)`.
Run the command again with `FO_DEBUG=1` and attach the traceback to the issue, without keys or
private paths.

### A route stops

| Output | Meaning | Do |
|---|---|---|
| `route failed: independence rule: ...` | no candidate is left from a vendor other than the producer's | add a candidate from another vendor |
| `route failed: no usable candidate: ...` | every candidate is from a vendor that is not enabled | enable the vendor or change the candidates |
| `REJECTED (pick is not an allowed candidate): ...`, exit 2 | the engine picked a model that is not an allowed candidate (not in `candidates`, from a vendor that is not enabled, or dropped by the independence rule) | do not use the pick; route again or choose by hand |
| `refusing to send the request to http://...` | plain HTTP to another machine | use HTTPS, or set `jev.allow_insecure_http true` on a trusted private network |

### A vendor call fails

| Sign | Meaning | Do |
|---|---|---|
| 401, 403, "Please sign in", "Not logged in" | sign-in is missing | sign in; see above |
| 429, `RESOURCE_EXHAUSTED`, quota | a usage window is spent | wait or use a smaller tier; do not loop retries |
| "unknown model", 404 | the id is not offered to you today | run `fo.py refresh` and use a listed id |
| status `timeout` | the CLI did not finish in time | raise `--timeout` or split the packet |
| status `empty` or `failed` | no verdict | read stderr and run it again |

Still stuck? Read [how-it-works.md](how-it-works.md), or run `fo.py doctor --live` and keep its
output for an issue report. Remove keys and private paths first.
