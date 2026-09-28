#!/usr/bin/env bash
# Shared helpers. Source from other scripts; do not execute directly.
set -euo pipefail

BENCH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export BENCH_ROOT

load_config() {
  local example="$BENCH_ROOT/config.env.example"
  local local_cfg="$BENCH_ROOT/config.env"
  # shellcheck disable=SC1090
  set -a
  if [[ -f "$local_cfg" ]]; then
    # shellcheck disable=SC1091
    echo "Localconfig $local_cfg loaded"
    source "$local_cfg"
  else
    echo "Localconfig $local_cfg not loaded"
  fi
  echo $SIRIUS_ROOT
  set +a

  SIRIUS_ROOT="${SIRIUS_ROOT:-$HOME/sirius}"
  DATA_ROOT="${DATA_ROOT:-$HOME/data/tpch}"
  PARQUET_ROOT="${PARQUET_ROOT:-$DATA_ROOT}"
  SPILL_DIR="${SPILL_DIR:-$DATA_ROOT/spill}"
  TELEMETRY_DIR="${TELEMETRY_DIR:-$DATA_ROOT/telemetry}"
  LOG_DIR="${LOG_DIR:-$DATA_ROOT/logs}"
  YAML_PATH="${YAML_PATH:-$DATA_ROOT/sirius.yaml}"
  SCALE_FACTORS="${SCALE_FACTORS:-100 200 300}"
  PROBE_QUERIES="${PROBE_QUERIES:-1 6 9}"
  PHASE1_ITERATIONS="${PHASE1_ITERATIONS:-1}"
  PHASE3_ITERATIONS="${PHASE3_ITERATIONS:-2}"
  FORCE_DISK_QUERY="${FORCE_DISK_QUERY:-9}"
  FORCE_DISK_SF="${FORCE_DISK_SF:-300}"
  FORCE_DISK_HOST_CAPS="${FORCE_DISK_HOST_CAPS:-40GiB 16GiB 8GiB}"
  HOST_CAPACITY="${HOST_CAPACITY:-8GiB}"
  DISK_CAPACITY="${DISK_CAPACITY:-200GiB}"
  QUENT_PORT="${QUENT_PORT:-8080}"
  ENGINE_NAME="${ENGINE_NAME:-siriusDB}"
  QUENT_EXPORTER="${QUENT_EXPORTER:-postcard}"
}

require_sirius() {
  if [[ ! -d "$SIRIUS_ROOT" ]]; then
    echo "SIRIUS_ROOT does not exist: $SIRIUS_ROOT" >&2
    echo "Clone Sirius and set SIRIUS_ROOT in config.env." >&2
    exit 1
  fi
  if [[ ! -x "$SIRIUS_ROOT/build/release/duckdb" ]]; then
    echo "DuckDB binary missing at $SIRIUS_ROOT/build/release/duckdb" >&2
    echo "Run: ./scripts/build-sirius.sh" >&2
    exit 1
  fi
  if [[ ! -x "$SIRIUS_ROOT/test/tpch_performance/generate_tpch_data.sh" ]]; then
    echo "TPC-H generator missing under $SIRIUS_ROOT/test/tpch_performance/" >&2
    exit 1
  fi
}

ensure_dirs() {
  mkdir -p "$DATA_ROOT" "$SPILL_DIR" "$TELEMETRY_DIR" "$LOG_DIR"
}

parquet_dir() {
  local sf="$1"
  echo "$PARQUET_ROOT/sf${sf}"
}

pixi_in_sirius() {
  (cd "$SIRIUS_ROOT" && pixi run -- "$@")
}

log() {
  printf '[%s] %s\n' "$(date -Is)" "$*"
}

init_run_id() {
  BENCH_RUN_ID="${BENCH_RUN_ID:-$(date -u +%Y%m%dT%H%M%S%NZ)_$$}"
  if [[ ! "$BENCH_RUN_ID" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "BENCH_RUN_ID must contain only letters, digits, underscores, or hyphens" >&2
    exit 2
  fi
  export BENCH_RUN_ID
  RUN_LOG_DIR="$LOG_DIR/$BENCH_RUN_ID"
  mkdir -p "$RUN_LOG_DIR"
}
