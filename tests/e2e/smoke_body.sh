#!/usr/bin/env bash
# The body of tests/e2e/smoke.sh. Do not run it directly: tests/e2e/smoke.sh starts
# tests/e2e/supervise.py, and only the supervisor runs this file, as
# `bash smoke_body.sh --token-fd N`. It refuses to run unless it can prove that the server at
# FO_OPENJEV_URL is its supervisor's: it reads a one-time token from file descriptor N (a pipe
# from the supervisor) and the server must answer GET /fo-smoke-token with the same token. No
# environment variable can stand in for that proof. Then it removes inherited FO_*, XDG_*,
# TYPESAFE_*, CODIV_*, OPENJEV_* and proxy variables itself, and sets the engine and telemetry off.
set -euo pipefail

# Shell functions inherited through the environment could replace any command below.
for f in $(compgen -A function); do unset -f "$f"; done

refuse() { echo "smoke_body.sh: $*. Run tests/e2e/smoke.sh instead." >&2; exit 3; }

[ "$#" = 2 ] && [ "$1" = --token-fd ] || refuse "no token from the supervisor"
case "$2" in '' | *[!0-9]*) refuse "the token file descriptor is not a number" ;; esac
TOKEN=""
{ IFS= read -r TOKEN <&"$2"; } 2> /dev/null || refuse "cannot read the token from the supervisor"
eval "exec $2<&-"
[ "${#TOKEN}" = 32 ] || refuse "the token from the supervisor is not valid"

SINK="${FO_OPENJEV_URL:-}"
[[ "$SINK" =~ ^http://127\.0\.0\.1:[0-9]+$ ]] || refuse "FO_OPENJEV_URL is not a loopback sink"
PY="${PYTHON:-python3}"

for name in $(compgen -e); do
  case "$name" in FO_* | XDG_* | TYPESAFE_* | CODIV_* | OPENJEV_*) unset "$name" ;; esac
  case "$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')" in
    http_proxy | https_proxy | all_proxy | no_proxy | ftp_proxy) unset "$name" ;;
  esac
done

# The proof: the sink must echo the token. No proxy, so the request goes to the sink itself.
ECHO="$("$PY" -I -c '
import sys, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    print(opener.open(sys.argv[1] + "/fo-smoke-token", timeout=5).read().decode("ascii", "replace").strip())
except Exception:
    pass
' "$SINK")" || true
[ "$ECHO" = "$TOKEN" ] || refuse "the server at FO_OPENJEV_URL is not this run's supervisor"
unset TOKEN ECHO

export FO_OPENJEV_URL="$SINK" FO_TELEMETRY_ENDPOINT="$SINK/fo-test-sink" FO_JEV_BACKEND=off FO_TELEMETRY=0 DO_NOT_TRACK=1
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
FO="$ROOT/skills/frontier-orchestration/scripts/fo.py"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

export HOME="$WORK/home" FO_PATH_EXTRA="" FO_HOST=unknown
mkdir -p "$HOME" "$WORK/bin"
cd "$HOME"
"$PY" -c "import sys; sys.path[:0] = ['$ROOT/tests']; from test_installer import signed_in_clis; signed_in_clis('$WORK/bin')"
export PATH="$WORK/bin:/usr/bin:/bin"
fo() { "$PY" -I "$FO" "$@"; }
step() { printf '\n== %s\n' "$*"; }
no_traceback() { if grep -q -e Traceback -e 'fo.py failed:' "$1"; then cat "$1"; exit 1; fi; }

step "route --demo"
fo route --demo

step "doctor before setup: exit 1 and says to run setup first"
set +e; fo doctor > "$WORK/doctor0.txt" 2>&1; code=$?; set -e
cat "$WORK/doctor0.txt"; no_traceback "$WORK/doctor0.txt"
[ "$code" = 1 ] && grep -q "setup has not run yet" "$WORK/doctor0.txt"

step "setup, non-interactive"
fo setup --non-interactive --jev off --providers claude,codex,gemini --telemetry no

step "doctor, providers"
fo doctor
fo providers

step "every --help"
for cmd in install setup doctor providers refresh route jev eval ask lessons config telemetry version; do
  fo "$cmd" --help > /dev/null
done
for sub in init recall add reinforce contradict retire run-new run-close lint; do
  fo lessons "$sub" --help > /dev/null
done
fo --help > /dev/null

step "eval with engine off"
fo eval routing

step "installer under a pseudo-terminal, engine off"
rm -f "$HOME/.config/frontier-orchestration/config.json"
DRIVE=("$PY" -I "$ROOT/tests/e2e/drive_installer.py" --fo "$FO" --python "$PY" --timeout 120
       --answer 'Which decision engine=4' --answer 'Which providers=1,2,3' --answer 'Share anonymous=2'
       --answer 'learning ledger=' --transcript "$WORK/install.log")
if [ "$(uname)" = Darwin ]; then
  script -q /dev/null "${DRIVE[@]}" < /dev/null | tee "$WORK/drive.txt"
else
  script -qec "$(printf '%q ' "${DRIVE[@]}")" /dev/null < /dev/null | tee "$WORK/drive.txt"
fi
no_traceback "$WORK/install.log"
# The summary is the last line; a pseudo-terminal may put control echo (^D) in front of it.
tail -n 1 "$WORK/drive.txt" | tr -d '\r' | "$PY" -c 'import json, sys; t = sys.stdin.read(); s = json.loads(t[t.index("{"):]); print("installer exit", s["exit"]); sys.exit(s["exit"])'
fo doctor

step "smoke run ok"
