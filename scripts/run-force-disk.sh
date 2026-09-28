#!/usr/bin/env bash
# Shrink HOST_CAPACITY through FORCE_DISK_HOST_CAPS and re-run one query until
# Quent task records mention DISK (or the list is exhausted).
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_config
require_sirius
ensure_dirs
init_run_id
log "benchmark run: $BENCH_RUN_ID"

pq="$(parquet_dir "$FORCE_DISK_SF")"
if [[ ! -d "$pq" ]]; then
  echo "missing parquet $pq — run ./scripts/generate-tpch.sh first" >&2
  exit 1
fi

helper="$SIRIUS_ROOT/test/tpch_performance/run_tpch_parquet_and_generate_telemetry.sh"
export SIRIUS_CONFIG_FILE="$YAML_PATH"

count_disk() {
  python3 - "$TELEMETRY_DIR" "$1" <<'PY'
import sys
from pathlib import Path
root = Path(sys.argv[1])
label = sys.argv[2].encode()
if not root.exists():
    print(0)
    raise SystemExit
newest = None
newest_mtime = -1.0
for p in root.iterdir():
    if p.is_dir() and (p / "task").exists() and (p / "query").exists():
        if not any(label in f.read_bytes() for f in (p / "query").iterdir() if f.is_file()):
            continue
        m = p.stat().st_mtime
        if m > newest_mtime:
            newest_mtime = m
            newest = p
if newest is None:
    print(0)
    raise SystemExit
blob = b"".join(f.read_bytes() for f in (newest / "task").iterdir() if f.is_file())
print(blob.count(b"DISK"))
PY
}

for cap in $FORCE_DISK_HOST_CAPS; do
  HOST_CAPACITY="$cap"
  export HOST_CAPACITY
  "$BENCH_ROOT/scripts/render-sirius-yaml.sh" >/dev/null
  tag="host${cap}"
  log_file="$RUN_LOG_DIR/force_disk_sf${FORCE_DISK_SF}_q${FORCE_DISK_QUERY}_${tag}.log"
  note="spill_sf${FORCE_DISK_SF}_${tag}_run_${BENCH_RUN_ID}"
  log "force-disk SF${FORCE_DISK_SF} q${FORCE_DISK_QUERY} HOST_CAPACITY=${cap}"
  {
    echo "=== force-disk start $(date -Is) host=${cap} ==="
    pixi_in_sirius "$helper" \
      --config "$YAML_PATH" \
      --parquet-dir "$pq" \
      --iterations 1 \
      --note "$note" \
      "$FORCE_DISK_SF" "$FORCE_DISK_QUERY"
    echo "=== force-disk end $(date -Is) ==="
  } 2>&1 | tee "$log_file"
  disk_hits="$(count_disk "$note")"
  log "task DISK hits in matching session: ${disk_hits}; spill=$(du -sh "$SPILL_DIR" | awk '{print $1}')"
  if [[ "$disk_hits" -gt 0 ]]; then
    log "DISK observed at HOST_CAPACITY=${cap}. Stop shrinking."
    echo "$cap" > "$RUN_LOG_DIR/host_cap_that_hit_disk.txt"
    exit 0
  fi
done

log "no DISK in FORCE_DISK_HOST_CAPS=${FORCE_DISK_HOST_CAPS}. Shrink further or raise FORCE_DISK_SF."
exit 2
