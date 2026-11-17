# Documentation

These guides are for people and for LLMs. Each page covers one topic. Every `fo.py` command on
these pages exists. Run `python3 -I skills/frontier-orchestration/scripts/fo.py --help` for the
full list. Every `fo.py` subcommand accepts `--help` and prints its flags without doing any work.

In the commands below, `fo.py` means
`python3 -I skills/frontier-orchestration/scripts/fo.py` from a clone of this repository, or
the same script in the installed plugin folder.

| Page | Read it to |
|---|---|
| [install.md](install.md) | install on Claude Code, Codex, Antigravity or any agent; run the installer; choose an engine; plan memory for OpenJev |
| [how-it-works.md](how-it-works.md) | understand the routing flow, the confidence x stakes gate and the local heuristic |
| [cost.md](cost.md) | send cheap work to cheap models, with the measured evidence |
| [eval.md](eval.md) | measure a decision engine before you trust it |
| [providers.md](providers.md) | reach Claude, Codex and Gemini read-only through `fo.py ask` |
| [telemetry.md](telemetry.md) | see what opt-in telemetry sends, and turn it on or off |
| [troubleshooting.md](troubleshooting.md) | read `fo.py doctor` output and fix common failures |

## Other sources

- [SKILL.md](../skills/frontier-orchestration/SKILL.md): the rules an agent follows.
- [references/](../skills/frontier-orchestration/references/): providers, pipelines, the
  ledger, measured evidence, confirmed rules, privacy.
- [PRIVACY.md](../PRIVACY.md): the full privacy notice.
- [CHANGELOG.md](../CHANGELOG.md): what changed in each release.
- [CONTRIBUTING.md](../CONTRIBUTING.md): how to change the code and run the tests.
- [llms.txt](../llms.txt): a map of this repository for LLMs.
