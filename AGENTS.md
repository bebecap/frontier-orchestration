# AGENTS.md

Instructions for coding agents that work on this repository. People: read
[CONTRIBUTING.md](CONTRIBUTING.md) first; this file is the short form.

## Setup

- Python 3.9 or later, on macOS or Linux. Standard library only: there is nothing to install.
- Node.js 18 or later only for the installer prototype in `npm/`.
- Run every script with `python3 -I` (isolated mode). The scripts import only their own folder.

## Test commands

Run all three before you say a change is done. CI runs the first two on macOS and Ubuntu, the
third and the collector tests on Ubuntu with Node 22, and `tests/e2e/smoke.sh` on Ubuntu.

```bash
python3 -m unittest discover -s tests
python3 -I skills/frontier-orchestration/scripts/fo.py route --check
(cd npm && npm test)
```

Also run `claude plugin validate --strict .` if you have Claude Code, after a change to a
manifest, `SKILL.md` or `commands/`.

The tests never call a vendor CLI. Network paths use real local HTTP servers, and telemetry
goes to a loopback sink, never to the production collector. `tests/helpers.py` gives each
command run a temporary home folder, which is also its current folder, and a clean environment.
The installer's OpenJev check and the telemetry sink go to a loopback server that the test
process starts on a free port and keeps for its whole run; it answers every request with 503. So
a real OpenJev on 127.0.0.1:8080 cannot change a test, and no other program owns that port.
`tests/e2e/smoke.sh` does the same: it always starts `tests/e2e/supervise.py`, which also removes
inherited `FO_*` and proxy variables and runs `tests/e2e/smoke_body.sh`. The body runs only when
the server at `FO_OPENJEV_URL` echoes a one-time token that the supervisor gave it on a pipe.

## Layout

- `skills/frontier-orchestration/`: the skill. `SKILL.md`, `scripts/fo*.py`, `data/`, `evals/`,
  `references/`. skills.sh copies only this folder, so it must not reference files outside it.
- `.claude-plugin/`, `.codex-plugin/`, `.agents/plugins/`, `.antigravity-plugin/`: host
  manifests. `commands/`: the Claude Code `/frontier-orchestration:setup` command.
- `scripts/prepublish_check.py`: the release gate for a history before it is published.
- `tests/`: the Python suite. `npm/`: the unpublished installer prototype. `collector/`: the
  telemetry collector. `docs/`: guides. `llms.txt`: the repository map.

## Code rules

- Every behavior change comes with a test that fails before the change and passes after it.
  Break the code on purpose once to prove the test can go red.
- The version is in six places: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`,
  `.codex-plugin/plugin.json`, `.antigravity-plugin/plugin.json`, `npm/package.json` and
  `VERSION` in `scripts/fo_config.py`. A test checks that they agree.
- A new model in `data/roster.json` needs a vendor, a tier and a description.
- A new telemetry field needs a change to `PRIVACY.md`, the allowlist test, the schema version
  and `CHANGELOG.md`.
- Docs state only what the code does. A measured number needs its run in `docs/eval.md` or
  `docs/cost.md`.

## Security

- Never write an API key to the config, the ledger, the telemetry log, the output or a commit.
  Keys come from an environment variable, the macOS Keychain, or a file with mode 0600.
- Provider asks stay read-only. Never remove the read-only or plan mode from a provider command.
- Never send telemetry to the production collector from a test or a script.
- Never commit `.privacy-denylist`, `.env*` or `*.key`. The privacy test blocks home paths, email
  addresses and key patterns in shipped files.
- Report a vulnerability as [SECURITY.md](SECURITY.md) says, not in a public issue.
