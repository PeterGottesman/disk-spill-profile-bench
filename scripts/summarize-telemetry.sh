#!/usr/bin/env bash
# Summarize Quent postcard sessions: labels, NVTX gpu_to_host_chunked, task HOST/DISK.
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_config

TELEMETRY_DIR="${TELEMETRY_DIR:-$DATA_ROOT/telemetry}"
if [[ ! -d "$TELEMETRY_DIR" ]]; then
  echo "TELEMETRY_DIR does not exist: $TELEMETRY_DIR" >&2
  exit 1
fi

python3 - "$TELEMETRY_DIR" <<'PY'
import re, sys
from pathlib import Path

root = Path(sys.argv[1])
print(f"{'session':<40} {'labels':<42} {'gpu_to_host':>11} {'HOST':>6} {'DISK':>6} {'MiB':>8}")
for sess in sorted(root.iterdir()):
    if not sess.is_dir():
        continue
    qdir = sess / "query"
    nv = sess / "NvtxEvent"
    task = sess / "task"
    labels = []
    if qdir.exists():
        blob = b"".join(f.read_bytes() for f in qdir.iterdir() if f.is_file())
        text = "".join(chr(b) if 32 <= b < 127 else " " for b in blob)
        labels = sorted(set(re.findall(r"(?:full_sf\d+|spill_sf\d+[A-Za-z0-9_]*)", text)))
    nvblob = b"".join(f.read_bytes() for f in nv.iterdir() if f.is_file()) if nv.exists() else b""
    tblob = b"".join(f.read_bytes() for f in task.iterdir() if f.is_file()) if task.exists() else b""
    size = sum(f.stat().st_size for f in sess.rglob("*") if f.is_file()) / (1024 * 1024)
    print(
        f"{sess.name:<40} {','.join(labels)[:42]:<42} "
        f"{nvblob.count(b'gpu_to_host_chunked'):11d} "
        f"{tblob.count(b'HOST'):6d} {tblob.count(b'DISK'):6d} {size:8.1f}"
    )
PY

echo
echo "spill dir: $(du -sh "$SPILL_DIR" 2>/dev/null | awk '{print $1}')"
echo "telemetry: $(du -sh "$TELEMETRY_DIR" | awk '{print $1}')"
