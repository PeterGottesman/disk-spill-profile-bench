#!/usr/bin/env bash
# End-to-end: detect → build → generate parquet → render yaml → phase 1 →
# force-disk (optional) → phase 3 → summarize.
#
# Skip steps with flags. Does not start Quent (run ./scripts/run-quent.sh after).
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_config

SKIP_BUILD=0
SKIP_GENERATE=0
SKIP_FORCE_DISK=0
SKIP_PHASE3=0

usage() {
  cat <<EOF
Usage: scripts/run-all.sh [options]

  --skip-build         Assume Sirius is already built
  --skip-generate      Assume parquet already exists under DATA_ROOT/sf*
  --skip-force-disk    Do not walk FORCE_DISK_HOST_CAPS
  --skip-phase3        Stop after phase 1 (and optional force-disk)
  -h, --help           Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1 ;;
    --skip-generate) SKIP_GENERATE=1 ;;
    --skip-force-disk) SKIP_FORCE_DISK=1 ;;
    --skip-phase3) SKIP_PHASE3=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

"$BENCH_ROOT/scripts/detect-hardware.sh"
if [[ "$SKIP_BUILD" -eq 0 ]]; then
  "$BENCH_ROOT/scripts/build-sirius.sh"
fi
if [[ "$SKIP_GENERATE" -eq 0 ]]; then
  "$BENCH_ROOT/scripts/generate-tpch.sh"
fi
"$BENCH_ROOT/scripts/render-sirius-yaml.sh"
"$BENCH_ROOT/scripts/run-phase1.sh"
if [[ "$SKIP_FORCE_DISK" -eq 0 ]]; then
  "$BENCH_ROOT/scripts/run-force-disk.sh" || true
fi
if [[ "$SKIP_PHASE3" -eq 0 ]]; then
  "$BENCH_ROOT/scripts/run-phase3.sh"
fi
"$BENCH_ROOT/scripts/summarize-telemetry.sh"
log "next: ./scripts/run-quent.sh && ./scripts/quent-links.sh --landing"
