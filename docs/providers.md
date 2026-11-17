# Providers

> `fo.py` on this page is short for `python3 -I <skill folder>/scripts/fo.py`. See
> the top of [install.md](install.md) for the skill folder.

A provider is a vendor whose models the skill may use: Claude, Codex (GPT) or Antigravity
(Gemini). The skill reaches each vendor through that vendor's own CLI, in read-only mode. There
is no bridge, MCP server or background daemon.

Agent-level detail (every flag, notes per CLI, failure signs) is in
[`references/providers.md`](../skills/frontier-orchestration/references/providers.md).

## How each vendor is reached

| Provider | Transports, in auto order | Read-only command that `fo.py ask` runs | Sign-in check |
|---|---|---|---|
| Claude | the Agent tool when you run in Claude Code; else the `claude` CLI | `claude -p --permission-mode plan` | `claude auth status` |
| Codex | the `codex` CLI | `codex exec --sandbox read-only` | `codex login status` |
| Antigravity (Gemini) | `agy`; else the `gemini` CLI | `agy --print --mode plan`; `gemini --approval-mode plan` | `agy models` lists Gemini ids |

`fo.py providers` shows, for this machine, how each vendor is reached and if its CLI is signed
in. Add `--json` for code.

## Choose providers

The installer asks which providers the skill may use. With flags:

```bash
fo.py setup --providers claude,codex,gemini
```

To turn one vendor off, or to force a transport:

```bash
fo.py config set providers.gemini.enabled false
fo.py config set providers.codex.via cli
```

For an independent review you need at least two vendors. With one vendor, `fo.py doctor` prints
a note: the different-vendor review rule cannot be met.

## Sign in

| Provider | Sign in with |
|---|---|
| Claude | `claude auth login` |
| Codex | `codex login` |
| Antigravity | run `agy`, sign in in the browser, then type `/exit` |
| gemini CLI | set `GEMINI_API_KEY`, or run `gemini` once to sign in |

`fo.py doctor --live` checks the sign-in of each enabled CLI.

## The `fo.py ask` contract

`fo.py ask` sends one prompt to one vendor and writes the answer to a file.

```bash
fo.py ask codex  --prompt-file packet.md --cwd /path/to/repo
fo.py ask gemini --prompt "Review this plan: ..." --out answer.md
fo.py ask claude --prompt-file packet.md          # from a Codex or agy host
```

Options: `--prompt-file F` or `--prompt TEXT` (or the prompt on stdin), `--model M`,
`--effort E`, `--cwd DIR`, `--out FILE`, `--timeout S`, `--json`.

The contract:

- **Read-only.** The command lines above are fixed. The config cannot replace them.
- **No shell.** The command is an argument list.
- **One answer file.** The default is a new private file in
  `~/.local/state/frontier-orchestration/asks/`.
- **A status.** `ok`, `failed`, `timeout` or `empty`. Only `ok` is a verdict. Never count another
  status as approve or block.
- **The model.** `fo.py ask` uses the `--model` flag first. Without the flag, it uses
  `providers.<vendor>.model` from the config. Without both, it gives no model to the CLI, so the
  CLI uses its own default model.
- **Only the Claude `host` transport is refused.** On a Claude Code host, Claude is reached
  through the `host` transport, and `fo.py ask claude` refuses it with "you are Claude Code: use
  the Agent tool for Claude". Use the Agent tool for Claude work there.
- **A same-vendor call through a CLI is not stopped.** `fo.py ask` does not compare the vendor
  you ask with the vendor of the host. For example, `fo.py ask codex` on a Codex host runs, and
  `fo.py ask claude` on a Claude Code host runs when `providers.claude.via` is `cli`. A
  same-vendor answer is not an independent review, so the caller must keep reviews cross-vendor.
- **Routing enforces independence.** When a task in a route spec has `needs_independence: yes`,
  `fo.py route` needs its `producer_vendor` (claude, codex or gemini). It removes the candidates
  of that vendor and the judge (Jev). If no candidate is left, or `producer_vendor` is missing,
  `fo.py route` stops with "route failed: independence rule: ..." and exits 1. It then gives no
  decision for any task.
- **Route exit codes.** `fo.py route` exits 1 when the route fails (no candidate is left, the
  spec is not correct, or Jev gives an error). It exits 2 when it returns at least one REJECT
  decision (the judge picked a model that is not an allowed candidate). Else it exits 0.
- **Exit code.** 0 when the status is `ok`, else 1.

Prompts and repository content go to the vendor you call, under that vendor's terms.

## Before a workflow

Send one tiny ask to each provider you will use, and read the answer:

```bash
fo.py ask codex  --prompt "Reply with one line: your model name. Liveness check." --effort low
fo.py ask gemini --prompt "Reply with one line: your model name. Liveness check."
```

`fo.py refresh` lists the models each CLI offers today. Use an id from that list.

Next: [telemetry.md](telemetry.md).
