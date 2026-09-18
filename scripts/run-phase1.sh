#!/usr/bin/env bash
# Probe: each scale factor × PROBE_QUERIES, PHASE1_ITERATIONS, Quent on.
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_config
require_sirius
ensure_dirs
"$BENCH_ROOT/scripts/render-sirius-yaml.sh" >/dev/null

export SIRIUS_CONFIG_FILE="$YAML_PATH"
helper="$SIRIUS_ROOT/test/tpch_performance/run_tpch_parquet_and_generate_telemetry.sh"

for sf in $SCALE_FACTORS; do
  pq="$(parquet_dir "$sf")"
  if [[ ! -d "$pq" ]]; then
    echo "missing parquet $pq — run ./scripts/generate-tpch.sh first" >&2
    exit 1
  fi
  log_file="$LOG_DIR/phase1_sf${sf}.log"
  log "Phase 1 SF${sf} queries=${PROBE_QUERIES} -> $log_file"
  {
    echo "=== Phase 1 SF${sf} start $(date -Is) host=${HOST_CAPACITY} ==="
    pixi_in_sirius "$helper" \
      --config "$YAML_PATH" \
      --parquet-dir "$pq" \
      --iterations "$PHASE1_ITERATIONS" \
      --note "spill_sf${sf}" \
      "$sf" $PROBE_QUERIES
    echo "=== Phase 1 SF${sf} end $(date -Is) ==="
  } 2>&1 | tee "$log_file"
done

log "spill=$(du -sh "$SPILL_DIR" | awk '{print $1}') telemetry=$(du -sh "$TELEMETRY_DIR" | awk '{print $1}')"
log "summarize with: ./scripts/summarize-telemetry.sh"
