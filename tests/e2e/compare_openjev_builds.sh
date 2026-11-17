#!/bin/sh
# Compare DiffusionGemma MLX builds for OpenJev with `fo.py eval`, one build at a time.
#
#   tests/e2e/compare_openjev_builds.sh OUT_DIR SPEC [SPEC ...]
#
# SPEC is REPO@REVISION (downloaded at that exact revision) or a local folder with MLX weights.
#
# Needs an OpenJev MLX install made by `fo.py install` (its venv has huggingface_hub and mlx-vlm).
# For each build: download that exact revision, start OpenJev on a free port with those weights, run
# `fo.py eval all --repeat 2`, record load time, disk size and peak physical memory, then stop it.
# Run it from the repository root. Apple silicon only. Nothing else on the machine is changed.
# Exit 0 only when every build loaded and passed its eval; 1 otherwise; 2 for a setup problem.
set -u
OUT=$1; shift
OJ=${FO_OPENJEV_HOME:-$HOME/.local/share/frontier-orchestration/openjev}
PY=$OJ/venv/bin/python
mkdir -p "$OUT"
fail=0   # any build that does not load, or whose eval does not pass, makes the script exit 1
pid=""
trap '[ -n "$pid" ] && kill $pid 2>/dev/null' EXIT        # never leave a server holding memory
trap 'exit 130' INT TERM
[ -x "$PY" ] || { echo "no OpenJev venv at $OJ"; exit 2; }
case "$(uname -sm)" in "Darwin arm64") ;; *) echo "Apple silicon only"; exit 2;; esac

for spec in "$@"; do
  t0=$(date +%s)
  if [ -d "$spec" ]; then
    name=local_$(basename "$spec")_$(printf %s "$spec" | cksum | cut -d' ' -f1); path=$spec  # unique per folder
    echo "=== local $spec"
  else
    repo=${spec%@*}; rev=${spec#*@}
    name=$(echo "$repo" | tr '/' '_')_$(echo "$rev" | cut -c1-12)  # one name per revision
    echo "=== $repo @ $rev"
    path=$("$PY" -c "import sys; from huggingface_hub import snapshot_download as s; print(s(sys.argv[1], revision=sys.argv[2]))" "$repo" "$rev" 2>"$OUT/$name.download.log" | tail -1)
  fi
  t1=$(date +%s)
  if [ ! -d "$path" ]; then echo "download failed: $(tail -3 "$OUT/$name.download.log")"; fail=1; continue; fi
  echo "downloaded in $((t1 - t0)) s: $(du -shL "$path" | cut -f1)"
  # A fresh free port per build, and the listener must be the server started here: never another one.
  PORT=$("$PY" -c "import socket; s=socket.socket(); s.bind(('127.0.0.1', 0)); print(s.getsockname()[1]); s.close()")
  rm -f "$OUT/$name.eval.json" "$OUT/$name.eval.txt"
  OPENJEV_BACKEND=mlx OPENJEV_MLX_MODEL="$path" OPENJEV_MLX_CACHE_LIMIT_GB=4 OPENJEV_PORT=$PORT \
    nohup "$PY" -m openjev > "$OUT/$name.server.log" 2>&1 &
  pid=$!
  up=0; i=0
  while [ $i -lt 180 ]; do
    if ! kill -0 $pid 2>/dev/null; then break; fi
    if lsof -nP -a -p $pid -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1 &&
       curl -s -m 3 "http://127.0.0.1:$PORT/v1/models" >/dev/null; then up=1; break; fi
    sleep 5; i=$((i + 1))
  done
  t2=$(date +%s)
  if [ $up -ne 1 ]; then
    echo "server did not start (exit or 15 min timeout); log tail:"; tail -8 "$OUT/$name.server.log"
    kill $pid 2>/dev/null; wait $pid 2>/dev/null; pid=""; fail=1; continue
  fi
  echo "loaded in $((t2 - t1)) s"
  FO_JEV_BACKEND=custom FO_JEV_BASE_URL=http://127.0.0.1:$PORT FO_JEV_MODEL=openjev-latest FO_TELEMETRY=0 \
    /usr/bin/python3 -I skills/frontier-orchestration/scripts/fo.py eval all --backend custom --repeat 2 \
    --out "$OUT/$name.eval.json" > "$OUT/$name.eval.txt" 2>&1
  rc=$?
  echo "eval exit $rc"
  [ $rc -eq 0 ] || fail=1
  # RSS misses the Metal buffers that hold MLX weights; the physical footprint counts them.
  /usr/bin/footprint -p $pid 2>/dev/null | grep -E "phys_footprint(_peak)?:" || echo "footprint unavailable"
  kill $pid 2>/dev/null; wait $pid 2>/dev/null; pid=""
  if [ ! -s "$OUT/$name.eval.json" ]; then echo "no report from this run:"; tail -5 "$OUT/$name.eval.txt"; fail=1; continue; fi
  /usr/bin/python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
s = d['suites']
print('verdict', d['verdict'], '| sanity', s['sanity']['accuracy'], '| routing pick', s['routing']['accuracy'],
      '| stakes', s['routing']['stakes_accuracy'], 'brier', s['routing']['stakes_brier'],
      '| errors', s['sanity']['errors'] + s['routing']['errors'],
      '| stability', s['sanity']['stability'], s['routing']['stability'],
      '| p50 ms', s['sanity']['latency']['p50_ms'], s['routing']['latency']['p50_ms'])
" "$OUT/$name.eval.json" 2>/dev/null || tail -5 "$OUT/$name.eval.txt"
done
exit $fail
