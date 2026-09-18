#!/usr/bin/env bash
# Parse DuckDB .timer lines from a phase log into a per-query table.
#
# Usage:
#   ./scripts/extract-timings.sh "$DATA_ROOT/logs/phase3_sf100.log"
set -euo pipefail
if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <phase-log>" >&2
  exit 2
fi
python3 - "$1" <<'PY'
import re, sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(errors="replace")
headers = list(re.finditer(r"=============== Q(\d+) iter (\d+) ===============", text))
byq = {}
for i, m in enumerate(headers):
    q, it = int(m.group(1)), int(m.group(2))
    start = m.end()
    end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
    times = re.findall(r"Run Time \(s\): real ([0-9.]+)", text[start:end])
    t = float(times[-1]) if times else None
    byq.setdefault(q, {})[it] = t
if not byq:
    print("no query timings found", file=sys.stderr)
    raise SystemExit(1)
iters = sorted({it for d in byq.values() for it in d})
hdr = f"{'Q':>3}" + "".join(f"{'iter'+str(it):>10}" for it in iters)
print(hdr)
for q in sorted(byq):
    cells = []
    for it in iters:
        v = byq[q].get(it)
        cells.append(f"{v:10.3f}" if v is not None else f"{'—':>10}")
    print(f"{q:3d}" + "".join(cells))
print("sum" + "".join(
    f"{sum(byq[q][it] for q in byq if it in byq[q]):10.3f}" for it in iters
))
PY
