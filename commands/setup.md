---
description: Set up frontier-orchestration on this machine (Jev backend, providers, telemetry consent)
---

Set up the frontier-orchestration skill for this user.

A person at a terminal gets the best experience from the interactive installer:
`python3 -I "<plugin root>/skills/frontier-orchestration/scripts/fo.py" install`. Tell the user that
command. You cannot drive its terminal UI yourself, so the steps below use flags.

`FO` below means `python3 -I "<plugin root>/skills/frontier-orchestration/scripts/fo.py"`. In Claude
Code the plugin root is `${CLAUDE_PLUGIN_ROOT}`. On other hosts it is the installed plugin folder
that holds `skills/frontier-orchestration/`.

1. Run `FO setup --non-interactive`.
2. If it exits 3 ("SETUP NEEDS ANSWERS"), ask the user each listed question with its options (use
   AskUserQuestion when you have it). Never answer a question for the user, and never pick the
   telemetry answer yourself.
3. Run `FO setup` again with the answers as flags (`--jev`, `--providers`, `--telemetry`).
4. If it prints "API key ... NOT FOUND", show the user the exact command it printed. The user runs
   it; never ask the user to paste a key into the chat.
5. Run `FO doctor --live` and report each FAIL or note in one line.
