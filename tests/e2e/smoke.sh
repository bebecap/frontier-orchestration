#!/usr/bin/env bash
# End-to-end smoke run of the command line, as a new user runs it, with the engine off and no
# network: route --demo, doctor (before and after setup), providers, setup, every --help, eval,
# and the interactive installer driven under a pseudo-terminal. CI runs it on Ubuntu; it runs on
# macOS too. Vendor CLIs are fakes that only report "signed in". Usage: tests/e2e/smoke.sh
#
# This entry point always starts tests/e2e/supervise.py; nothing in the environment can skip it.
# The supervisor owns a loopback 503 server for the whole run, removes inherited FO_* and proxy
# variables, and runs tests/e2e/smoke_body.sh, which refuses to run unless the server at
# FO_OPENJEV_URL echoes a one-time token the supervisor gave it on a pipe. So a real OpenJev on
# 127.0.0.1:8080 is never contacted.
set -euo pipefail
[ "$#" = 0 ] || { echo "usage: tests/e2e/smoke.sh (no arguments)" >&2; exit 2; }
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
exec "${PYTHON:-python3}" -I "$ROOT/tests/e2e/supervise.py"
