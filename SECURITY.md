# Security

## Report a problem

Use GitHub's private vulnerability reporting on this repository (Security > Report a
vulnerability). Do not open a public issue for a security problem. Expect a first answer within
7 days.

## Design rules this project keeps

- No API key is ever written to the config, the ledger, the telemetry log or the output. Keys
  come from an environment variable, the macOS Keychain, or a file with mode 0600.
- Config, telemetry id and telemetry log files are written atomically with mode 0600.
- Reviews are read-only by contract: every provider template uses the vendor's read-only or plan
  mode. A template that removes that is a security bug.
- The router fails closed: a candidate whose vendor it cannot name is dropped from a task that
  needs an independent reviewer.
- Ledger free text is folded to one line, so input cannot forge a Markdown heading or a
  frontmatter key.
- Scripts run with `python3 -I` (isolated mode) and import only their own folder.
- The telemetry sender only posts to `https://` endpoints (or `http://127.0.0.1` for tests).
