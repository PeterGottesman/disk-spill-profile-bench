#!/usr/bin/env bash
# Generate TPC-H parquet for each SCALE_FACTORS entry under DATA_ROOT/sf<SF>.
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_config
require_sirius
ensure_dirs

jobs_args=()
if [[ -n "${GENERATE_JOBS:-}" ]]; then
  jobs_args+=(--jobs "$GENERATE_JOBS")
fi

for sf in $SCALE_FACTORS; do
  out="$(parquet_dir "$sf")"
  if [[ -d "$out" ]]; then
    log "skip SF${sf}: $out already exists (delete to regenerate)"
    continue
  fi
  log "generating SF${sf} parquet -> $out"
  pixi_in_sirius bash test/tpch_performance/generate_tpch_data.sh \
    "$sf" --format parquet --output "$out" "${jobs_args[@]}"
  du -sh "$out"
done

log "done. parquet under $PARQUET_ROOT"
df -h "$PARQUET_ROOT"
