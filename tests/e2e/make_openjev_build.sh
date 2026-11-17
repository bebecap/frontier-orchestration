#!/bin/sh
# Make a smaller DiffusionGemma MLX build for OpenJev, with the vision tower kept (OpenJev loads
# the whole model through mlx-vlm; a text-only build fails with "Missing ... parameters").
#
#   tests/e2e/make_openjev_build.sh OUT_DIR NAME [mlx_vlm.convert options...]
#   e.g.  tests/e2e/make_openjev_build.sh ~/fo-e2e/conv dg-3bit-g64 -q --q-bits 3 --q-group-size 64
#
# Source: google/diffusiongemma-26B-A4B-it at a pinned revision (bf16, about 48 GB, not gated).
set -eu
OUT=$1; NAME=$2; shift 2
OJ=${FO_OPENJEV_HOME:-$HOME/.local/share/frontier-orchestration/openjev}
PY=$OJ/venv/bin/python
SRC=google/diffusiongemma-26B-A4B-it
REV=f7f5b7f5fa82ffc52addd066915886d497f5517b
mkdir -p "$OUT"
[ -e "$OUT/$NAME" ] && { echo "$OUT/$NAME exists; remove it first"; exit 2; }
src=$("$PY" -c "import sys; from huggingface_hub import snapshot_download as s; print(s(sys.argv[1], revision=sys.argv[2]))" "$SRC" "$REV" | tail -1)
echo "source: $src ($(du -shL "$src" | cut -f1))"
t0=$(date +%s)
"$PY" -m mlx_vlm.convert --hf-path "$src" --mlx-path "$OUT/$NAME" "$@" > "$OUT/$NAME.convert.log" 2>&1
echo "converted in $(( $(date +%s) - t0 )) s: $(du -sh "$OUT/$NAME" | cut -f1)"
