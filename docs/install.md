# Install and set up

This page tells you how to install the skill on each host, how to run the installer, and how to
choose a decision engine. It also tells you how much memory OpenJev needs on a Mac.

Requirements: Python 3.9 or later, on macOS or Linux. The skill uses only the Python standard
library. You do not install packages.

**The `fo.py` command.** In these docs, `fo.py` is short for the full command:

```bash
python3 -I <skill folder>/scripts/fo.py
```

The skill folder is `skills/frontier-orchestration` in a clone, the folder that `npx skills add`
or your host's plugin manager made, and `${CLAUDE_SKILL_DIR}` inside Claude Code. `fo.py` alone
is not a command on your `PATH`. The installer, `setup` and `doctor` print their hints with the
full path on your machine, so you can copy them as they are.

Hosts: the hosted engines (TypeSafe Jev, OpenJev on Codiv) and engine `off` work on macOS and
Linux. Self-hosted OpenJev needs Apple silicon on a Mac, or an NVIDIA GPU and Docker with the
NVIDIA runtime on Linux.

## 1. Install the skill on your host

### Claude Code

```bash
claude plugin marketplace add bebecap/frontier-orchestration
claude plugin install frontier-orchestration@frontier-orchestration
```

Then run `/frontier-orchestration:setup` in a session. This command asks the setup questions for
you (see [commands/setup.md](../commands/setup.md)).

### OpenAI Codex

```bash
codex plugin marketplace add bebecap/frontier-orchestration
codex plugin add frontier-orchestration@frontier-orchestration
```

### Antigravity (agy)

```bash
git clone https://github.com/bebecap/frontier-orchestration
agy plugin install ./frontier-orchestration
```

### Any other agent (skills.sh)

```bash
npx skills add bebecap/frontier-orchestration --skill frontier-orchestration
```

skills.sh copies only the skill folder (`skills/frontier-orchestration/`). Everything the skill
needs is in that folder.

## 2. Run the installer

The examples below run from a clone of this repository. On a plugin install, use the installed
plugin folder that holds `skills/frontier-orchestration/`.

```bash
python3 -I skills/frontier-orchestration/scripts/fo.py install
```

The installer asks four questions. It writes the answers to
`~/.config/frontier-orchestration/config.json`. It never writes a key to that file.

1. **Decision engine.** TypeSafe Jev, OpenJev on this machine, OpenJev hosted on Codiv, or no
   engine (local rules).
2. **Providers.** Claude, Codex and Antigravity (Gemini). You pick providers, not models. At
   least one must be installed and signed in. The installer checks this and shows how to sign
   in.
3. **Telemetry.** Yes or no. See [telemetry.md](telemetry.md).
4. **Ledger folder.** The folder for lessons and runs. The default is
   `~/.local/share/frontier-orchestration/ledger`. Give a full path that starts with `/` or `~`.
   The installer refuses a relative path, because it would depend on the folder you run the
   command from.

Then check the result:

```bash
python3 -I skills/frontier-orchestration/scripts/fo.py doctor --live
```

`doctor --live` makes one tiny engine call. It also checks that each enabled provider CLI is
signed in.

## 3. Set up with flags (agents and scripts)

An agent cannot drive the terminal UI. It uses `fo.py setup` with flags.

```bash
fo.py setup --non-interactive
fo.py setup --jev typesafe --providers claude,codex,gemini --telemetry no
```

| Flag | Values |
|---|---|
| `--jev` | `typesafe`, `openjev`, `codiv`, `custom`, `off` |
| `--jev-url URL` | the server address (needed for `custom`) |
| `--jev-model ID` | the engine model id |
| `--providers` | any of `claude,codex,gemini` |
| `--telemetry` | `yes` or `no` |
| `--ledger PATH` | the ledger folder: a full path (starts with `/` or `~`) |
| `--ledger-log PATH` | the ledger log file: a full path (starts with `/` or `~`) |
| `--upgrade-clis` | `yes` or `no`: lets `fo.py refresh` update the vendor CLIs |
| `--non-interactive` | never prompt, also in a terminal |

When an answer is missing, `setup` exits 3 and prints the questions. The agent asks the user each
question. **The agent never answers for the user, above all the telemetry question.** Flag setup
does not check sign-in. Run `fo.py doctor --live` after it.

If `setup` prints `API key ... NOT FOUND`, it also prints the ways to store the key that work on
this machine, as full commands. The user runs one of them. Never paste a key into a chat.

## 4. Choose a decision engine

| Engine | What it is | Key |
|---|---|---|
| `typesafe` | TypeSafe's hosted Jev (`https://api.typesafe.ai`, model `jev-latest`) | `TYPESAFE_API_KEY` |
| `openjev` | [OpenJev](https://github.com/razorback16/openjev), open weights, self-hosted (`http://127.0.0.1:8080`, model `openjev-latest`) | none, or `OPENJEV_API_KEY` |
| `codiv` | OpenJev hosted on [Codiv](https://codiv.ai) (`https://api.codiv.ai`) | `CODIV_API_KEY` |
| `custom` | any server that speaks `POST /v1/systemone` (set `jev.base_url`) | `FO_JEV_API_KEY` |
| `off` | no engine: local rules, no network, no key | none |

To compare engines, see [eval.md](eval.md). In short: TypeSafe Jev scored best on the measured
datasets. Engine `off` costs nothing and needs nothing.

### Where keys live

When `jev.key_source` is not set, the skill reads a key from these places, in this order:

1. the environment variable (for example `TYPESAFE_API_KEY`);
2. on macOS, the login Keychain item with the same name;
3. a file named by `jev.api_key_file`, with mode `0600`.

When `jev.key_source` is set (`env`, `keychain` or `file`), the skill reads the key only from
that source. It does not try the other two. `fo.py install` sets `jev.key_source` when you pick
or add a TypeSafe or Codiv key.

To store a key in the Keychain without shell history (macOS):

```bash
security add-generic-password -U -a "$USER" -s TYPESAFE_API_KEY -w
```

On Linux (no Keychain), use one of these:

- `fo.py install` and choose "Add a new key": it writes a key file with mode `0600`;
- your own file that holds only the key: `chmod 600` it, then
  `fo.py config set jev.api_key_file /path/to/the/file jev.key_source file`;
- `export TYPESAFE_API_KEY=...` in your shell profile.

### Transport rules

Engine requests use HTTPS. Plain HTTP goes only to `127.0.0.1`, `localhost` or `[::1]`, unless
you set `jev.allow_insecure_http`. A URL with a user name or password, or one that cannot be read
(bad brackets, a port that is not a number from 0 to 65535), is refused. The client refuses
redirects. It never shows a server's error text.

Proxies: plain HTTP requests and requests to this machine (`127.0.0.1`, `localhost`, `[::1]`)
never go through a proxy, whatever `http_proxy`, `https_proxy`, `all_proxy` or a system proxy
say. A proxy would otherwise get the prompt and the key in plaintext. HTTPS requests to another
host use the proxy from the environment as usual: the proxy sees only the host name, and TLS runs
end to end. The same rules apply to the installer's OpenJev check and to the telemetry sender.

## 5. OpenJev on this machine

### On a Mac

OpenJev runs on Apple silicon only, through MLX. Its model is DiffusionGemma 26B-A4B. The
installer asks which build to download. Each build is pinned to an exact Hugging Face revision,
so a later upload cannot change it.

| Build | Hugging Face repository | Download |
|---|---|---|
| Compact (the default) | `mlx-community/diffusiongemma-26B-A4B-it-mxfp4` | about 14 GB |
| Standard (4-bit) | `mlx-community/diffusiongemma-26B-A4B-it-4bit` | about 16 GB |
| Another build | any full DiffusionGemma MLX build, as `owner/name[@revision]` | varies |

Three numbers are about size and memory. Do not mix them.

| Number | What it is | Note |
|---|---|---|
| about 14 GB | the download size of the compact build | disk space, not memory |
| about 19 GB | the peak memory of the compact build after an eval | with the default 1 GB MLX buffer cap; about 22 GB with a 4 GB cap |
| 24 GB | the Mac memory to plan for | less memory makes the system swap |

Smaller community builds do not load:

- The oQ builds leave out the vision tower. MLX needs the full model.
- The TurboQuant builds use a quantization mode that MLX does not know.

After you install another build, check it with `fo.py eval` (see [eval.md](eval.md)).

The installer writes a launcher, `start-openjev.sh`, in
`~/.local/share/frontier-orchestration/openjev/`. The server log is `server.log` in the same
folder. The first start loads the weights into memory. This takes a minute or two.

### Memory pressure

OpenJev keeps the whole model in memory while it serves.

- Measured with the compact build on 2026-11-16 (`footprint`, physical memory; details in
  [eval.md](eval.md#memory)):

  | MLX buffer cap | Loaded, before an eval | After `fo.py eval` |
  |---|---|---|
  | 1 GB (the launcher default for the pinned builds) | not measured | peak 19 GB |
  | 4 GB | 14 GB, peak 15 GB | 21 GB, peak 22 GB |

  The 19 GB figure is from one run. The 22 GB peak was measured twice. The eval answers were
  the same with both caps.
- A launcher written by an installer before 2026-11-16 uses a 4 GB cap: `start-openjev.sh` then
  has `OPENJEV_MLX_CACHE_LIMIT_GB:-4`. To use 1 GB, start it with
  `OPENJEV_MLX_CACHE_LIMIT_GB=1`, or change `:-4` to `:-1` in that file.
- **Plan for a Mac with 24 GB of memory or more.**
- On a smaller Mac the system starts to swap (it moves memory to disk). Then each decision gets
  much slower. To see this, open Activity Monitor > Memory. The memory pressure graph turns
  yellow or red.

What to do:

1. Close large apps before you start OpenJev.
2. Set `OPENJEV_MLX_CACHE_LIMIT_GB` to change the buffer cap. The launcher uses 1 GB for the two
   pinned builds when this variable is not set.
3. If memory is still tight, use an engine that downloads nothing: TypeSafe Jev, OpenJev on
   Codiv, or engine `off`.

The installer also warns you when the machine has less memory or free disk than the build needs.

### On another machine

On a machine that is not Apple silicon, the installer uses OpenJev's Docker image. It needs an
NVIDIA GPU with 24 GB of memory or more, and Docker with the NVIDIA runtime. You can also point
the skill at an OpenJev server that you already run:

```bash
fo.py config set jev.backend openjev
fo.py config set jev.base_url https://gpu-box.internal:8443
```

## 6. Configuration reference

```bash
fo.py config show                                  # the effective config and resolved paths
fo.py config path                                  # where the config file is
fo.py config get jev.backend
fo.py config set jev.backend openjev
fo.py config set jev.allow_insecure_http true      # only for plain http on a trusted private network
fo.py config set ledger.path ~/Vault/learning/frontier-orchestration
fo.py config set refresh.upgrade_clis true         # let refresh update agy, claude, codex
fo.py config set providers.codex.enabled false     # turn a vendor off
fo.py config set ledger.path ~/notes ledger.log null   # several keys in one call, checked together
```

`config set` checks the whole config after the change. With two broken keys (for example a
relative `ledger.path` and a relative `ledger.log`), fix both in one call as above. Spaces around
a path value are removed before it is checked, saved and used.

Environment overrides: `FO_CONFIG`, `FO_JEV_BACKEND`, `FO_JEV_BASE_URL`, `FO_JEV_MODEL`,
`FO_LEDGER`, `FO_LEDGER_LOG`, `FO_TELEMETRY`, `TYPESAFE_BASE_URL` (typesafe engine only),
`DO_NOT_TRACK`, `FO_OPENJEV_URL` (where `fo.py install` looks for OpenJev on this machine, waits
for it and saves it as `jev.base_url`; `https://`, or `http://` to `127.0.0.1`, `localhost` or
`[::1]`, with a valid port and no user name; scheme and host may be in any case, but
`localhost.` with a trailing dot is not accepted; any other value, also one that cannot be read,
is ignored with a warning; the default is `http://127.0.0.1:8080`, and the launcher and Docker
command the installer writes always serve there), `FO_DEBUG=1` (show the Python traceback of an
unexpected error), `FO_PATH_EXTRA` (the folders where the skill looks for vendor CLIs, separated
by colons). `FO_PATH_EXTRA` replaces the default list. After `PATH`, the skill searches
`~/.local/bin`, `/opt/homebrew/bin` and `/usr/local/bin`. If you set `FO_PATH_EXTRA`, include
those folders in it if you need them.

When `FO_JEV_BACKEND` names a different engine than the config, the config's URL, model and key
settings for its own engine are ignored. A key never goes to another engine's server.

Model roster overrides go in `~/.config/frontier-orchestration/roster.json`. It has the same
shape as [`data/roster.json`](../skills/frontier-orchestration/data/roster.json). A `null` value
removes an entry.

Next: [how-it-works.md](how-it-works.md).
