#!/usr/bin/env bash
# Start (or reuse) the Quent UI over the TPC-H spill telemetry directory.
#
# Default data: $TELEMETRY_DIR (config.env / DATA_ROOT/telemetry).
# Phase 3 sessions to open in the UI:
#   full_sf100 / full_sf200 / full_sf300
#
# Usage:
#   ./scripts/run-quent.sh
#   ./scripts/run-quent.sh --restart          # kill whatever owns QUENT_PORT first
#   TELEMETRY_DIR=/path/to/telemetry ./scripts/run-quent.sh
#   ./scripts/run-quent.sh --foreground       # do not background the server
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_config
ensure_dirs

RESTART=0
FOREGROUND=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --restart) RESTART=1 ;;
    --foreground) FOREGROUND=1 ;;
    -h|--help)
      cat <<EOF
Usage: scripts/run-quent.sh [--restart] [--foreground]

  --restart      Kill the process listening on QUENT_PORT, then start Quent
  --foreground   Run sirius-telemetry-server in this terminal (Ctrl-C stops it)

Environment / config.env:
  SIRIUS_ROOT    Sirius checkout (pixi run quent)
  TELEMETRY_DIR  Postcard sessions (default: \$DATA_ROOT/telemetry)
  QUENT_PORT     UI port (default: 8080)
EOF
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 2
      ;;
  esac
  shift
done

TELEMETRY_DIR="${TELEMETRY_DIR:-$DATA_ROOT/telemetry}"
QUENT_PORT="${QUENT_PORT:-8080}"
LOG_DIR="${LOG_DIR:-$DATA_ROOT/logs}"
mkdir -p "$LOG_DIR"

if [[ ! -d "$TELEMETRY_DIR" ]]; then
  echo "TELEMETRY_DIR does not exist: $TELEMETRY_DIR" >&2
  exit 1
fi
if [[ ! -d "$SIRIUS_ROOT" ]]; then
  echo "SIRIUS_ROOT does not exist: $SIRIUS_ROOT" >&2
  exit 1
fi

port_pid() {
  ss -ltnp 2>/dev/null | awk -v p=":${QUENT_PORT}" '$4 ~ p"$" {print}' | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -n1
}

list_full_sessions() {
  python3 - "$TELEMETRY_DIR" <<'PY'
import re, sys
from pathlib import Path
root = Path(sys.argv[1])
rows = []
for sess in sorted(root.iterdir()):
    qdir = sess / "query"
    if not sess.is_dir() or not qdir.exists():
        continue
    blob = b"".join(f.read_bytes() for f in qdir.iterdir() if f.is_file())
    text = "".join(chr(b) if 32 <= b < 127 else " " for b in blob)
    labels = sorted({re.split(r"_tpch_q\d+_iter\d+", m)[0] for m in re.findall(r"full_sf\d+(?:_[A-Za-z0-9_-]+)?", text)})
    if not labels:
        continue
    rows.append((",".join(labels), sess.name))
if not rows:
    print("  (no full_sf* query labels found under this telemetry dir)")
    raise SystemExit
for labels, sid in rows:
    print(f"  {labels}  session {sid}")
PY
}

if [[ "$RESTART" -eq 1 ]]; then
  pid="$(port_pid || true)"
  if [[ -n "${pid:-}" ]]; then
    log "killing pid $pid on :$QUENT_PORT"
    kill "$pid" 2>/dev/null || true
    sleep 1
  fi
fi

existing="$(port_pid || true)"
if [[ -n "${existing:-}" && "$FOREGROUND" -eq 0 ]]; then
  log "Quent already listening on :$QUENT_PORT (pid $existing)"
  log "UI: http://localhost:${QUENT_PORT}"
  log "telemetry: $TELEMETRY_DIR"
  echo
  echo "Phase 3 sessions:"
  list_full_sessions
  echo
  echo "Pass --restart to recycle the server, --foreground to take over this terminal."
  exit 0
fi

log "starting Quent UI on :$QUENT_PORT from $TELEMETRY_DIR"
log "UI: http://localhost:${QUENT_PORT}"
echo "Phase 3 sessions:"
list_full_sessions
echo

if [[ "$FOREGROUND" -eq 1 ]]; then
  cd "$SIRIUS_ROOT"
  exec pixi run quent "$TELEMETRY_DIR"
fi

log_file="$LOG_DIR/quent_ui.log"
cd "$SIRIUS_ROOT"
nohup pixi run quent "$TELEMETRY_DIR" >>"$log_file" 2>&1 &
echo $! >"$LOG_DIR/quent_ui.pid"
log "background pid $!  log $log_file"

for _ in $(seq 1 30); do
  if [[ -n "$(port_pid || true)" ]]; then
    log "listening on http://localhost:${QUENT_PORT}"
    exit 0
  fi
  sleep 1
done

echo "Quent did not bind :$QUENT_PORT. Last log lines:" >&2
tail -n 40 "$log_file" >&2
exit 1
