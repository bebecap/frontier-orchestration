# Providers: how each vendor is reached

The skill talks to each vendor through that vendor's own CLI, in read-only mode, with one
command: `fo.py ask`. There is no bridge, MCP server or background daemon. `fo.py providers`
shows what this machine has; `fo.py install` (or `setup`) chooses which providers may be used.

| Provider | Transports (auto order) | Read-only mode `fo.py ask` uses | Sign-in check |
|---|---|---|---|
| Claude | `host` (you are Claude Code: use the Agent tool), `cli` | `claude -p --permission-mode plan` | `claude auth status` |
| Codex | `cli` | `codex exec --sandbox read-only --ephemeral -o <file> -` | `codex login status` |
| Antigravity (Gemini) | `agy`, `gemini-cli` | `agy --print ... --mode plan`, `gemini --approval-mode plan` | `agy models` lists Gemini ids |

## `fo.py ask`

```
fo.py ask codex  --prompt-file review.md --cwd /path/to/repo [--model gpt-6.1-sol] [--effort high]
fo.py ask gemini --prompt "Review this plan: ..." --out answer.md
fo.py ask claude --prompt-file packet.md            # from a Codex or agy host
```

- The command is built as an argument list and run without a shell. The prompt goes on stdin
  (Claude, Codex, gemini CLI) or as one argument (agy).
- The answer is written to `--out` (default `~/.local/state/frontier-orchestration/asks/`, one new
  private file per run). The run locks that path until it ends: an old answer there is emptied
  first, so a failed run never leaves a stale verdict, and a path another run still holds is
  refused (status `failed`) and left as it is.
- Status: `ok`, `failed` (non-zero exit), `timeout` (default 30 minutes; the whole process group
  is stopped), `empty` (exit 0 but no answer). Only `ok` is a verdict.
- No model given: the CLI's own default model is used. Pick a model only when a route says so.
- From inside Claude Code, Claude is reached with the Agent tool. `fo.py ask claude` refuses on a
  Claude Code host because a same-vendor answer is not an independent review.
- Long reviews: run the command in the background (your host's background shell) and read the
  out file when it ends.

The command lines above cannot be replaced from the config. An earlier `providers.<vendor>.command`
override was removed: no check could tell reliably whether a custom command line keeps the CLI
read-only. A config that still has the key loads with it ignored (never run), and `fo.py doctor`
says to delete it. If a CLI is not on `PATH`, add its folder to `FO_PATH_EXTRA`.

## Notes per CLI

- **codex**: `--sandbox read-only` is the review contract; never pass
  `--dangerously-bypass-approvals-and-sandbox`. Model ids your account can use are in
  `~/.codex/models_cache.json`; `fo.py refresh` reads it.
- **agy**: give `--print-timeout` a unit (`0s`). Do not add `--dangerously-skip-permissions`. Do
  not add `--disable-slash-commands`: with it, agy ignores `--mode plan`. `agy models` lists
  today's ids. `--add-dir` is the project folder you pass as `--cwd`, never a vendor state folder
  (`~/.gemini`, `~/.codex`, `~/.claude`).
- **gemini CLI**: needs a Gemini API key or a Google sign-in that serves the model you want.
- Wrapping a consumer subscription in third-party tooling may break the vendor's terms and can
  suspend the account. Decide that for yourself; an API key avoids the question.
- Prompts and repository content go to the vendor you call.

## Liveness check before a workflow

Send one tiny ask per provider you will use, at low effort, and read the answer:

```
fo.py ask codex  --prompt "Reply with one line: your model name. Liveness check." --effort low
fo.py ask gemini --prompt "Reply with one line: your model name. Liveness check."
```

## Failure signs

| Symptom | Meaning | Do |
|---|---|---|
| 401, 403, "Please sign in", "Not logged in" | sign-in missing | stop; the user signs in (`fo.py providers` shows how) |
| 429, `RESOURCE_EXHAUSTED`, quota | a usage window is spent | wait or use a smaller tier; no retry loop |
| "unknown model", 404 | id not offered to you today | `fo.py refresh`, use a listed id |
| status `timeout` | the CLI did not finish in time | raise `--timeout` or split the packet |
| status `empty` or `failed` | no verdict | read stderr, re-run; never count it as approve or block |
