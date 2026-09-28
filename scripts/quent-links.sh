#!/usr/bin/env bash
# Print Quent UI URLs for labeled TPC-H queries.
#
# Requires the Quent server (./scripts/run-quent.sh). Pattern:
#   http://localhost:<port>/profile/engine/<engineId>/query/<queryId>
#
# Usage:
#   ./scripts/quent-links.sh
#   ./scripts/quent-links.sh full_sf100 full_sf200 full_sf300
#   ./scripts/quent-links.sh --landing          # one URL per session (q1 iter1)
#   ./scripts/quent-links.sh --markdown
#   ./scripts/quent-links.sh --json
#   QUENT_PORT=8080 ./scripts/quent-links.sh spill_sf300_host8
set -euo pipefail
# shellcheck source=lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
load_config

LANDING=0
FORMAT=text   # text | markdown | json
FILTERS=()

usage() {
  cat <<EOF
Usage: scripts/quent-links.sh [options] [label-prefix...]

  --landing      One landing URL per matching session (q1 iter1, else first query)
  --markdown     GitHub-flavored markdown table / list
  --json         JSON array on stdout
  -h, --help     Show this help

With no prefixes, print every labeled query Quent knows about.
Prefixes match the start of instance_name (e.g. full_sf100, spill_sf300_host8).

Environment / config.env:
  QUENT_PORT     UI/API port (default: 8080)
  TELEMETRY_DIR  used only to annotate session dirs when present
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --landing) LANDING=1 ;;
    --markdown) FORMAT=markdown ;;
    --json) FORMAT=json ;;
    -h|--help) usage; exit 0 ;;
    -*)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *) FILTERS+=("$1") ;;
  esac
  shift
done

QUENT_PORT="${QUENT_PORT:-8080}"
TELEMETRY_DIR="${TELEMETRY_DIR:-$DATA_ROOT/telemetry}"
API="http://localhost:${QUENT_PORT}/api"
UI="http://localhost:${QUENT_PORT}"

if ! curl -fsS --max-time 2 "$API/engines" >/dev/null; then
  echo "Quent is not reachable at $API" >&2
  echo "Start it with: ./scripts/run-quent.sh" >&2
  exit 1
fi

QUENT_LINK_PREFIXES=""
if [[ ${#FILTERS[@]} -gt 0 ]]; then
  QUENT_LINK_PREFIXES="$(printf '%s\n' "${FILTERS[@]}")"
fi
export API UI TELEMETRY_DIR LANDING FORMAT QUENT_LINK_PREFIXES
python3 - <<'PY'
import json, os, re, sys, urllib.request
from pathlib import Path

api = os.environ["API"]
ui = os.environ["UI"]
telemetry = Path(os.environ.get("TELEMETRY_DIR", ""))
landing = os.environ.get("LANDING") == "1"
fmt = os.environ.get("FORMAT", "text")
prefixes = [p for p in os.environ.get("QUENT_LINK_PREFIXES", "").split("\n") if p]


def get(path):
    with urllib.request.urlopen(api + path, timeout=60) as r:
        return json.loads(r.read())


def qkey(name: str):
    m = re.search(r"_q(\d+)_iter(\d+)$", name)
    return (int(m.group(1)), int(m.group(2))) if m else (999, 999)


def matches(name: str) -> bool:
    if not prefixes:
        return True
    return any(name.startswith(p) or p in name for p in prefixes)


def session_labels(sess: str):
    qdir = telemetry / sess / "query"
    if not qdir.exists():
        return []
    blob = b"".join(f.read_bytes() for f in qdir.iterdir() if f.is_file())
    text = "".join(chr(b) if 32 <= b < 127 else " " for b in blob)
    return sorted({re.split(r"_tpch_q\d+_iter\d+", m)[0] for m in re.findall(r"(?:full|spill)_sf\d+(?:_[A-Za-z0-9_-]+)?", text)})


engines = get("/engines?with_metadata=true")
session_to_engine = {}
for e in engines:
    eid = e["id"]
    ctx = get(f"/engines/{eid}/contexts")
    for sess in ctx.get("context_resources", {}):
        session_to_engine[sess] = eid

rows = []
for e in engines:
    eid = e["id"]
    sess = next((s for s, eng in session_to_engine.items() if eng == eid), "")
    groups = get(f"/engines/{eid}/query-groups")
    queries = []
    for g in groups:
        qs = get(f"/engines/{eid}/query_group/{g['id']}/queries")
        queries.extend(qs if isinstance(qs, list) else [qs])
    queries = [q for q in queries if matches(q.get("instance_name", ""))]
    queries.sort(key=lambda q: qkey(q.get("instance_name", "")))
    if not queries:
        continue
    chosen = queries
    if landing:
        chosen = [
            next(
                (q for q in queries if q.get("instance_name", "").endswith("_q1_iter1")),
                queries[0],
            )
        ]
    for q in chosen:
        name = q.get("instance_name", "")
        url = f"{ui}/profile/engine/{eid}/query/{q['id']}"
        rows.append(
            {
                "label": name,
                "session": sess,
                "engine_id": eid,
                "query_id": q["id"],
                "url": url,
                "session_labels": session_labels(sess) if sess else [],
            }
        )

if fmt == "json":
    json.dump(rows, sys.stdout, indent=2)
    sys.stdout.write("\n")
    raise SystemExit(0 if rows else 1)

if not rows:
    print("no matching queries", file=sys.stderr)
    raise SystemExit(1)

if landing or fmt == "markdown":
    if fmt == "markdown":
        print("| Label | Link |")
        print("|---|---|")
        for r in rows:
            print(f"| `{r['label']}` | {r['url']} |")
    else:
        # group by session label for landing
        for r in rows:
            tag = r["session_labels"][0] if r["session_labels"] else r["label"]
            print(f"{tag}\t{r['url']}")
    raise SystemExit(0)

current_engine = None
for r in rows:
    if r["engine_id"] != current_engine:
        current_engine = r["engine_id"]
        tags = ",".join(r["session_labels"]) or r["session"]
        print(f"\n# {tags}  engine {r['engine_id']}")
        if r["session"]:
            print(f"# session dir {r['session']}")
    print(f"{r['label']:40}  {r['url']}")
PY
