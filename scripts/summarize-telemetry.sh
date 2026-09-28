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

RUN_FILTER=""
case "${1:-}" in
  "") ;;
  --run-id)
    if [[ $# -ne 2 || -z "$2" ]]; then
      echo "Usage: $0 [--run-id ID]" >&2
      exit 2
    fi
    RUN_FILTER="$2"
    ;;
  *) echo "Usage: $0 [--run-id ID]" >&2; exit 2 ;;
esac

python3 - "$TELEMETRY_DIR" "$RUN_FILTER" <<'PYCODE'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
run_filter = sys.argv[2]
rows = []
for sess in sorted(root.iterdir()):
    if not sess.is_dir():
        continue
    qdir = sess / "query"
    nv = sess / "NvtxEvent"
    task = sess / "task"
    labels = []
    if qdir.exists():
        blob = b"".join(f.read_bytes() for f in qdir.iterdir() if f.is_file())
        # The Sirius helper appends _tpch_qN_iterK to the --note value.
        labels = sorted({
            re.split(r"_tpch_q\d+_iter\d+", m.decode())[0]
            for m in re.findall(rb"(?:full|spill)_sf\d+(?:_[A-Za-z0-9_-]+)?", blob)
        })
    run_ids = sorted({m.group(1) for label in labels if (m := re.search(r"_run_([A-Za-z0-9_-]+)$", label))})
    run_id = ",".join(run_ids) if run_ids else "(legacy)"
    if run_filter and run_filter not in run_ids:
        continue
    nvblob = b"".join(f.read_bytes() for f in nv.iterdir() if f.is_file()) if nv.exists() else b""
    tblob = b"".join(f.read_bytes() for f in task.iterdir() if f.is_file()) if task.exists() else b""
    size = sum(f.stat().st_size for f in sess.rglob("*") if f.is_file()) / (1024 * 1024)
    rows.append((run_id, ",".join(labels) or "(unlabeled)", sess.name,
                 nvblob.count(b"gpu_to_host_chunked"), tblob.count(b"HOST"),
                 tblob.count(b"DISK"), size))

headers = ("run ID", "labels", "session", "gpu_to_host", "HOST", "DISK", "MiB")
widths = [max(len(str(row[i])) for row in [headers, *rows]) for i in range(3)]
print(f"{headers[0]:<{widths[0]}}  {headers[1]:<{widths[1]}}  {headers[2]:<{widths[2]}}  {headers[3]:>11}  {headers[4]:>6}  {headers[5]:>6}  {headers[6]:>8}")
for run_id, labels, session, gpu, host, disk, size in rows:
    print(f"{run_id:<{widths[0]}}  {labels:<{widths[1]}}  {session:<{widths[2]}}  {gpu:11d}  {host:6d}  {disk:6d}  {size:8.1f}")
if not rows:
    print("(no matching sessions)")
PYCODE

echo
echo "spill dir: $(du -sh "$SPILL_DIR" 2>/dev/null | awk '{print $1}')"
echo "telemetry: $(du -sh "$TELEMETRY_DIR" | awk '{print $1}')"
