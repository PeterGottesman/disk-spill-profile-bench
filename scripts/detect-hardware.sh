#!/usr/bin/env bash
# Print detected GPU / RAM / disk and suggested config.env knobs.
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_config

echo "=== GPU ==="
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,compute_cap,memory.total,driver_version --format=csv
  cap="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -n1 | tr -d '[:space:]')"
  vram_mib="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1 | tr -d '[:space:]')"
  major="${cap%%.*}"
  minor="${cap#*.}"
  echo
  echo "Suggested CUDAARCHS=${major}${minor}-real"
  echo "Suggested GPU_USAGE_LIMIT_FRACTION=0.9   # ~$(( vram_mib * 9 / 10 )) MiB of ${vram_mib} MiB"
else
  echo "nvidia-smi not found. Set CUDAARCHS explicitly (e.g. 90a-real)."
fi

echo
echo "=== RAM ==="
if command -v free >/dev/null 2>&1; then
  free -h
  total_gib="$(free -g | awk '/^Mem:/{print $2}')"
  echo
  echo "# Host pinned budget: start large (no disk), then shrink until Quent task DISK > 0."
  echo "# This laptop needed 8GiB host to hit disk on SF300 q9 (62 GiB RAM, 8 GiB VRAM)."
  if [[ -n "${total_gib:-}" && "$total_gib" -ge 8 ]]; then
    echo "Suggested HOST_CAPACITY start: $(( total_gib * 60 / 100 ))GiB   # ~60% of ${total_gib} GiB"
    echo "Suggested FORCE_DISK_HOST_CAPS=\"$(( total_gib * 60 / 100 ))GiB $(( total_gib * 25 / 100 ))GiB $(( total_gib * 12 / 100 ))GiB\""
  fi
else
  echo "free not found."
fi

echo
echo "=== Disk for DATA_ROOT=${DATA_ROOT} ==="
df -h "$(dirname "$DATA_ROOT")" 2>/dev/null || df -h /
echo
echo "Parquet size on the reference box was ~0.26 GiB per SF unit"
echo "  SF100 ~26G, SF200 ~52G, SF300 ~79G. Budget extra for spill + telemetry."
echo
echo "=== Paths ==="
echo "SIRIUS_ROOT=$SIRIUS_ROOT"
echo "DATA_ROOT=$DATA_ROOT"
echo "Copy config.env.example -> config.env and edit the Suggested lines above."
