#!/usr/bin/env bash
# Full 22 TPC-H queries × PHASE3_ITERATIONS at each SCALE_FACTORS entry.
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_config
require_sirius
ensure_dirs
init_run_id
log "benchmark run: $BENCH_RUN_ID"
"$BENCH_ROOT/scripts/render-sirius-yaml.sh" >/dev/null

export SIRIUS_CONFIG_FILE="$YAML_PATH"
helper="$SIRIUS_ROOT/test/tpch_performance/run_tpch_parquet_and_generate_telemetry.sh"

for sf in $SCALE_FACTORS; do
  pq="$(parquet_dir "$sf")"
  if [[ ! -d "$pq" ]]; then
    echo "missing parquet $pq — run ./scripts/generate-tpch.sh first" >&2
    exit 1
  fi
  log_file="$RUN_LOG_DIR/phase3_sf${sf}.log"
  log "Phase 3 SF${sf} all 22 queries × ${PHASE3_ITERATIONS} -> $log_file"
  {
    echo "=== Phase 3 SF${sf} start $(date -Is) host=${HOST_CAPACITY} ==="
    pixi_in_sirius "$helper" \
      --config "$YAML_PATH" \
      --parquet-dir "$pq" \
      --iterations "$PHASE3_ITERATIONS" \
      --note "full_sf${sf}_run_${BENCH_RUN_ID}" \
      "$sf"
    echo "=== Phase 3 SF${sf} end $(date -Is) ==="
  } 2>&1 | tee "$log_file"
done

log "spill=$(du -sh "$SPILL_DIR" | awk '{print $1}') telemetry=$(du -sh "$TELEMETRY_DIR" | awk '{print $1}')"
log "run=$BENCH_RUN_ID; summarize with: ./scripts/summarize-telemetry.sh --run-id $BENCH_RUN_ID"
log "Quent links: ./scripts/quent-links.sh --landing full_sf100 full_sf200 full_sf300"
