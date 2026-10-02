#!/usr/bin/env python3
"""Build a standalone HTML report and CSV/JSON measurements from a matrix manifest."""
import argparse
import csv
from collections import defaultdict
import hashlib
from html import escape
import json
from pathlib import Path
import re
import shlex
import shutil
import statistics

GIB = 2 ** 30
HEADER = re.compile(r'^=============== Q(\d+) iter (\d+) ===============$', re.M)
TIMER = re.compile(r'Run Time \(s\): real ([0-9.]+)')


def timings(path):
    content = path.read_text(errors='replace')
    matches = list(HEADER.finditer(content))
    found = {}
    for index, match in enumerate(matches):
        section = content[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(content)]
        values = TIMER.findall(section)
        if len(values) < 2:
            raise ValueError(f'no query timer in {path}: Q{match[1]} iteration {match[2]}')
        key = (int(match[1]), int(match[2]))
        if key in found:
            raise ValueError(f'duplicate timing {key} in {path}')
        found[key] = float(values[-1])
    return found


def metric(row, suffix):
    return sum(value for key, value in row['transition_logical_bytes'].items() if key.endswith('->' + suffix))


def count(row, suffix):
    return sum(value for key, value in row['transition_counts'].items() if key.endswith('->' + suffix))


def report_settings(manifest):
    """Read the immutable run snapshots, rather than the editable source config."""
    settings = None
    for run in manifest['runs']:
        values = {}
        for line in Path(run['config']).read_text().splitlines():
            if not line or line.startswith('#'):
                continue
            key, raw = line.split('=', 1)
            parsed = shlex.split(raw)
            if len(parsed) != 1:
                raise ValueError(f'invalid config snapshot value for {key}: {run["config"]}')
            values[key] = parsed[0]
        yaml = Path(run['yaml']).read_text()
        match = re.search(r'^\s+num_gpus:\s*(\d+)\s*$', yaml, re.M)
        if not match:
            raise ValueError(f'missing num_gpus in {run["yaml"]}')
        current = {
            'num_gpus': int(match[1]),
            'cuda_visible_devices': values.get('CUDA_VISIBLE_DEVICES', ''),
            'gpu_usage_limit_fraction': float(values['GPU_USAGE_LIMIT_FRACTION']) if values.get('GPU_USAGE_LIMIT_FRACTION') else None,
            'gpu_usage_limit_bytes': values.get('GPU_USAGE_LIMIT') or None,
        }
        if values.get('NUM_GPUS') and int(values['NUM_GPUS']) != current['num_gpus']:
            raise ValueError(f'GPU topology mismatch in {run["id"]}')
        if settings is not None and current != settings:
            raise ValueError(f'GPU settings differ across runs: {run["id"]}')
        settings = current
    return settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if manifest.get('schema_version') != 1:
        parser.error('unsupported manifest schema')
    scales, caps = manifest['scale_factors'], manifest['host_caps']
    queries = manifest['queries']
    iterations = manifest['iterations']
    settings = report_settings(manifest)
    expected = {(q, i) for q in queries for i in range(1, iterations + 1)}
    records = []
    for run in manifest['runs']:
        if run['status'] != 'complete':
            raise ValueError(f'run is not complete: {run["id"]}')
        telemetry = json.loads(Path(run['metrics']).read_text())
        for sf in scales:
            times = timings(Path(run['log_dir']) / f'phase3_sf{sf}.log')
            if set(times) != expected:
                raise ValueError(f'incomplete timings for {run["id"]}, SF{sf}')
            prefix = f'full_sf{sf}_run_{run["id"]}_'
            matched = [row for row in telemetry if row['label'].startswith(prefix)]
            by_key = {(row['query'], row['iteration']): row for row in matched}
            if set(by_key) != expected or len(by_key) != len(matched):
                raise ValueError(f'incomplete or duplicate telemetry for {run["id"]}, SF{sf}')
            for (q, i), runtime in sorted(times.items()):
                row = by_key[q, i]
                records.append({'scale_factor': sf, 'host_cap': run['host_cap'], 'repetition': run['repetition'],
                                'run_id': run['id'], 'query': q, 'iteration': i, 'runtime_seconds': runtime,
                                'num_gpus': settings['num_gpus'], 'cuda_visible_devices': settings['cuda_visible_devices'],
                                'gpu_usage_limit_fraction': settings['gpu_usage_limit_fraction'],
                                'gpu_usage_limit_bytes': settings['gpu_usage_limit_bytes'],
                                'host_placements': count(row, 'HOST'), 'host_logical_bytes': metric(row, 'HOST'),
                                'disk_placements': count(row, 'DISK'), 'disk_logical_bytes': metric(row, 'DISK'),
                                'host_input_tasks': row['tasks_prepared_with_host_input'],
                                'disk_input_tasks': row['tasks_prepared_with_disk_input']})
    expected_count = len(scales) * len(caps) * manifest['repetitions'] * len(expected)
    if len(records) != expected_count:
        raise ValueError(f'{len(records)} measurements; expected {expected_count}')
    warm = [r for r in records if r['iteration'] == iterations]
    by_group = defaultdict(list)
    for row in warm:
        by_group[row['scale_factor'], row['host_cap'], row['repetition']].append(row)
    per_rep = []
    for (sf, cap, rep), rows in sorted(by_group.items()):
        if len(rows) != len(queries):
            raise ValueError(f'incomplete warm query set: SF{sf}, {cap}, repetition {rep}')
        per_rep.append({'scale_factor': sf, 'host_cap': cap, 'repetition': rep,
                        'runtime_sum_seconds': sum(r['runtime_seconds'] for r in rows),
                        'host_queries': sum(r['host_placements'] > 0 for r in rows),
                        'disk_queries': sum(r['disk_placements'] > 0 for r in rows),
                        'host_placements': sum(r['host_placements'] for r in rows),
                        'disk_placements': sum(r['disk_placements'] for r in rows),
                        'host_logical_gib': sum(r['host_logical_bytes'] for r in rows) / GIB,
                        'disk_logical_gib': sum(r['disk_logical_bytes'] for r in rows) / GIB,
                        'disk_input_tasks': sum(r['disk_input_tasks'] for r in rows)})
    grouped = defaultdict(list)
    for row in per_rep:
        grouped[row['scale_factor'], row['host_cap']].append(row)
    summary = []
    fields = ['runtime_sum_seconds', 'host_queries', 'disk_queries', 'host_placements', 'disk_placements',
              'host_logical_gib', 'disk_logical_gib', 'disk_input_tasks']
    for sf in scales:
        for cap in caps:
            group = grouped[sf, cap]
            if len(group) != manifest['repetitions']:
                raise ValueError(f'incomplete repetitions: SF{sf}, {cap}')
            item = {'scale_factor': sf, 'host_cap': cap, 'repetitions': len(group)}
            for field in fields:
                item[field] = statistics.median(r[field] for r in group)
                if field == 'runtime_sum_seconds':
                    item['runtime_min_seconds'] = min(r[field] for r in group)
                    item['runtime_max_seconds'] = max(r[field] for r in group)
            summary.append(item)
    baseline = {r['scale_factor']: r['runtime_sum_seconds'] for r in summary if r['host_cap'] == caps[0]}
    for row in summary:
        row['runtime_ratio_to_reference'] = row['runtime_sum_seconds'] / baseline[row['scale_factor']]
    query_groups = defaultdict(list)
    for r in warm:
        query_groups[r['scale_factor'], r['host_cap'], r['query']].append(r)
    query_summary = []
    for sf in scales:
        for cap in caps:
            for q in queries:
                group = query_groups[sf, cap, q]
                row = {'scale_factor': sf, 'host_cap': cap, 'query': q, 'repetitions': len(group)}
                for field in ['runtime_seconds', 'host_placements', 'disk_placements', 'host_logical_bytes', 'disk_logical_bytes', 'disk_input_tasks']:
                    row[field] = statistics.median(r[field] for r in group)
                query_summary.append(row)
    query_baseline = {(r['scale_factor'], r['query']): r['runtime_seconds'] for r in query_summary if r['host_cap'] == caps[0]}
    for row in query_summary:
        reference = query_baseline[row['scale_factor'], row['query']]
        row['runtime_ratio_to_reference'] = row['runtime_seconds'] / reference if reference else None
    out = (args.output_dir or args.manifest.parent / 'report').resolve()
    out.mkdir(parents=True, exist_ok=True)
    snapshots = out / 'configs'
    snapshots.mkdir(exist_ok=True)
    for run in manifest['runs']:
        target = snapshots / run['id'] / 'config.env'
        target.parent.mkdir(exist_ok=True)
        shutil.copy2(run['config'], target)
    shutil.copy2(manifest['runs'][0]['config'], out / 'config.env')
    source = Path(manifest['config_source'])
    source_config_available = source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest() == manifest['config_sha256']
    if source_config_available:
        shutil.copy2(source, out / 'source-config.env')
    else:
        (out / 'source-config.env').unlink(missing_ok=True)
    (out / 'measurements.json').write_text(json.dumps({'manifest': str(args.manifest.resolve()), 'settings': settings, 'measurements': records,
                                                        'per_repetition': per_rep, 'summary': summary, 'query_summary': query_summary}, indent=2) + '\n')
    with (out / 'measurements.csv').open('w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    html = render(manifest, summary, query_summary, len(records), settings, source_config_available)
    (out / 'report.html').write_text(html)
    print(f'Wrote {out / "report.html"}, measurements.csv, measurements.json')


def render(manifest, summary, queries, measurement_count, settings, source_config_available):
    caps, scales = manifest['host_caps'], manifest['scale_factors']
    first, last = min(scales), max(scales)
    first_rows = {r['host_cap']: r for r in summary if r['scale_factor'] == first}
    last_rows = {r['host_cap']: r for r in summary if r['scale_factor'] == last}
    onsets = []
    for cap in caps:
        onset = next((r['scale_factor'] for r in summary if r['host_cap'] == cap and r['disk_queries'] > 0), None)
        onsets.append(f'{escape(cap)}: {"SF" + str(onset) if onset else "none observed"}')
    rows = []
    for sf in scales:
        for cap in caps:
            r = next(item for item in summary if item['scale_factor'] == sf and item['host_cap'] == cap)
            rows.append(f'<tr><th>SF{sf}</th><td>{escape(cap)}</td><td>{r["runtime_sum_seconds"]:.3f}</td>'
                        f'<td>{r["runtime_min_seconds"]:.3f}–{r["runtime_max_seconds"]:.3f}</td>'
                        f'<td>{r["runtime_ratio_to_reference"]:.2f}×</td><td>{r["host_queries"]:g}</td>'
                        f'<td>{r["host_placements"]:g}</td><td>{r["host_logical_gib"]:.2f}</td>'
                        f'<td>{r["disk_queries"]:g}</td><td>{r["disk_placements"]:g}</td>'
                        f'<td>{r["disk_logical_gib"]:.2f}</td><td>{r["disk_input_tasks"]:g}</td></tr>')
    dataset_rows = ''.join(f'<tr><th>SF{d["scale_factor"]}</th><td>{d["parquet_gib"]:.3f} GiB</td><td>{d["files"]}</td><td>{d["total_rows"]:,}</td></tr>' for d in sorted(manifest.get('datasets', []), key=lambda d: d['scale_factor']))
    payload = json.dumps({'summary': summary, 'queries': queries, 'scales': scales, 'caps': caps}, separators=(',', ':')).replace('</', '<\\/')
    machine = manifest.get('machine', {})
    gpu = ', '.join(machine.get('gpu') or []) or 'unavailable'
    source = f'{manifest["repetitions"]} repetition(s), {manifest["iterations"]} iteration(s), {len(scales)} scale factor(s), {len(caps)} host cap(s)'
    usage_limit = (f'{settings["gpu_usage_limit_fraction"]:g} of each GPU'
                   if settings['gpu_usage_limit_bytes'] is None else settings['gpu_usage_limit_bytes'])
    gpu_settings = (f'{settings["num_gpus"]} GPU(s); CUDA_VISIBLE_DEVICES={settings["cuda_visible_devices"] or "unset"}; '
                    f'GPU usage limit: {usage_limit}')
    config_links = ' · '.join(f'<a href="configs/{escape(run["id"])}/config.env">{escape(run["id"])}</a>'
                              for run in manifest['runs'])
    source_config_link = ' · <a href="source-config.env">Source config.env</a>' if source_config_available else ''
    storage = manifest.get('storage') or {}
    storage_note = f'<p><strong>Storage:</strong> {escape(storage["description"])}.</p>' if storage.get('description') else ''
    storage_note += f'<p>Dataset root: <code>{escape(manifest["parquet_root"])}</code>.</p>'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Spill scaling: {escape(manifest['experiment_id'])}</title><style>
:root{{font-family:system-ui,sans-serif;color:#182638;background:#f4f7fb}}*{{box-sizing:border-box}}body{{margin:0}}main{{max-width:1250px;margin:auto;padding:32px 20px 60px}}h1{{font-size:clamp(2rem,4vw,3rem);margin:.3em 0}}h2{{font-size:1.25rem;margin:0 0 14px}}p{{line-height:1.5}}section,.card{{background:white;border:1px solid #dce5ee;border-radius:12px;padding:20px;margin:16px 0;box-shadow:0 2px 10px #152a4008}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}}.cards .card{{margin:0}}.value{{font-size:1.6rem;font-weight:750}}.muted{{color:#526176}}.small{{font-size:.88rem}}.table{{overflow:auto}}table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}th,td{{text-align:right;padding:8px 9px;border-bottom:1px solid #e4eaf1;white-space:nowrap}}th:first-child,td:first-child{{text-align:left}}thead th{{font-size:.78rem;color:#46566a}}select{{padding:7px;border:1px solid #b6c5d5;border-radius:6px;margin:0 15px 10px 5px}}svg{{max-width:100%;height:auto}}a{{color:#2359a5}}code{{overflow-wrap:anywhere}}@media(max-width:720px){{.cards{{grid-template-columns:1fr}}section{{padding:15px}}}}
</style></head><body><main><p class="muted small">TPC-H benchmark · {escape(manifest['experiment_id'])}</p><h1>Runtime and spilling as scale grows</h1><p class="muted">{source}. {escape(gpu_settings)}. Each point uses the final iteration of all 22 queries. Across repetitions, the report shows medians and runtime ranges.</p>
<div class="cards"><div class="card"><div class="value">{measurement_count:,}</div><div class="muted">query iterations with timing and telemetry</div></div><div class="card"><div class="value">{last_rows[caps[-1]]['runtime_ratio_to_reference']:.2f}×</div><div class="muted">SF{last} runtime at {escape(caps[-1])} versus {escape(caps[0])}</div></div><div class="card"><div class="value">{last_rows[caps[-1]]['disk_logical_gib']:.1f} GiB</div><div class="muted">median logical placements into DISK at SF{last}, {escape(caps[-1])}</div></div></div>
<section><h2>What changed with scale</h2><p>At {escape(caps[0])}, the median 22-query runtime sum grew from <strong>{first_rows[caps[0]]['runtime_sum_seconds']:.3f} s at SF{first}</strong> to <strong>{last_rows[caps[0]]['runtime_sum_seconds']:.3f} s at SF{last}</strong>. At SF{last}, {escape(caps[-1])} took <strong>{last_rows[caps[-1]]['runtime_sum_seconds']:.3f} s</strong>, with <strong>{last_rows[caps[-1]]['disk_queries']:g} queries</strong> reaching DISK and <strong>{last_rows[caps[-1]]['disk_input_tasks']:g} tasks</strong> prepared with DISK input.</p><p><strong>First measured disk spill:</strong> {'; '.join(onsets)}. This is the first scale in the selected matrix, not an interpolated threshold.</p></section>
<section><h2>Runtime by scale factor</h2><svg id="chart" viewBox="0 0 920 400" role="img" aria-label="Runtime across scale factors"></svg><p class="small muted">Median sum of the 22 final-iteration runtimes for each repetition. Both axes use log scales when all values are positive.</p></section>
<section><h2>Runtime and spill by configuration</h2><div class="table"><table><thead><tr><th>Scale</th><th>HOST cap</th><th>Median runtime (s)</th><th>Range (s)</th><th>vs reference</th><th>HOST queries</th><th>HOST placements</th><th>HOST logical GiB</th><th>DISK queries</th><th>DISK placements</th><th>DISK logical GiB</th><th>Disk-input tasks</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div><p class="small muted">Queries, placements, volumes, and disk-input tasks are medians of repetition totals. HOST/DISK logical GiB sum batch <code>capacity_bytes</code> on entry to that tier. They are cumulative logical placements, not peak occupancy or physical device I/O.</p></section>
<section><h2>Explore individual queries</h2><label>Scale<select id="sf">{''.join(f'<option value="{sf}"{" selected" if sf == last else ""}>SF{sf}</option>' for sf in scales)}</select></label><label>HOST cap<select id="cap">{''.join(f'<option value="{escape(cap)}">{escape(cap)}</option>' for cap in caps)}</select></label><label>Sort<select id="sort"><option value="query">Query</option><option value="runtime_ratio_to_reference">Slowdown</option><option value="disk_logical_bytes">DISK volume</option></select></label><div class="table"><table><thead><tr><th>Query</th><th>Median runtime (s)</th><th>vs reference</th><th>HOST placements</th><th>HOST logical GiB</th><th>DISK placements</th><th>DISK logical GiB</th><th>Disk-input tasks</th></tr></thead><tbody id="queryrows"></tbody></table></div><p class="small muted">Per-query fields are medians across repetitions. The ratio divides the median runtime by the reference cap's median for that query.</p></section>
<section><h2>Inputs and reproducibility</h2><p>Machine: <code>{escape(machine.get('hostname', 'unknown'))}</code>; GPU: {escape(gpu)}; CPU: {escape(str(machine.get('cpu_model', 'unknown')))}; RAM: {(machine.get('memory_kib') or 0) / 1024**2:.1f} GiB. Sirius revision: <code>{escape(str((manifest.get('sirius_revision') or {}).get('commit', 'unknown')))}</code>. The first HOST cap, {escape(caps[0])}, is the runtime reference.</p><p><strong>GPU settings:</strong> {escape(gpu_settings)}.</p><p><a href="config.env">Reference run config.env</a>{source_config_link}</p><p class="small">Resolved config.env for every run: {config_links}</p><div class="table"><table><thead><tr><th>Dataset</th><th>Parquet size</th><th>Files</th><th>Rows</th></tr></thead><tbody>{dataset_rows}</tbody></table></div><p class="small muted">Datasets were checked for all eight tables, readable Parquet metadata, matching schemas, and row counts. Runs used separate config snapshots, rendered YAML, spill paths, telemetry, and logs. Query order was fixed, so small runtime differences can include caching and machine noise. HOST capacity is a configuration change; runtime association with spilling does not by itself isolate a causal cost.</p><p><a href="measurements.csv">Raw per-query CSV</a> · <a href="measurements.json">Measurements and summaries JSON</a></p></section>
<section><h2>Data location</h2>{storage_note}</section></main><script id="data" type="application/json">{payload}</script><script>
const data=JSON.parse(document.getElementById('data').textContent),colors=['#2563eb','#d97706','#c2410c','#7c3aed','#059669','#0891b2'];
function chart(){{
  const svg=document.getElementById('chart'),S=data.summary,xs=data.scales,ys=S.map(r=>r.runtime_sum_seconds);
  const left=100,right=860,top=65,bottom=335,minY=Math.min(...ys),maxY=Math.max(...ys);
  const lo=Math.log(minY),hi=maxY===minY?lo+1:Math.log(maxY);
  const X=sf=>left+(Math.log(sf)-Math.log(xs[0]))/(Math.log(xs.at(-1))-Math.log(xs[0])||1)*(right-left);
  const Y=y=>bottom-(Math.log(y)-lo)/(hi-lo)*(bottom-top);
  const tickLabel=tick=>Number(tick.toPrecision(3)).toLocaleString('en-US',{{maximumSignificantDigits:3}});
  let out=`<line x1="${{left}}" y1="${{bottom}}" x2="${{right}}" y2="${{bottom}}" stroke="#aab8c7"/><line x1="${{left}}" y1="${{top}}" x2="${{left}}" y2="${{bottom}}" stroke="#aab8c7"/>
    <text x="${{(left+right)/2}}" y="385" text-anchor="middle" font-size="13" fill="#46566a">TPC-H scale factor</text>
    <text x="22" y="${{(top+bottom)/2}}" transform="rotate(-90 22 ${{(top+bottom)/2}})" text-anchor="middle" font-size="13" fill="#46566a">Median total query runtime (s)</text>`;
  const ticks=[];
  for(let k=Math.floor(Math.log10(minY));k<=Math.ceil(Math.log10(maxY));k++){{
    for(const mult of [1,2,5]){{const tick=mult*10**k;if(tick>=minY&&tick<=maxY)ticks.push(tick)}}
  }}
  if(ticks.length<2){{ticks.length=0;ticks.push(minY);if(maxY>minY)ticks.push(maxY)}}
  ticks.forEach(tick=>{{const y=Y(tick);out+=`<line x1="${{left}}" y1="${{y}}" x2="${{right}}" y2="${{y}}" stroke="#e4eaf1"/><line x1="${{left-5}}" y1="${{y}}" x2="${{left}}" y2="${{y}}" stroke="#aab8c7"/><text x="${{left-9}}" y="${{y+4}}" text-anchor="end" font-size="12" fill="#526176">${{tickLabel(tick)}}</text>`}});
  xs.forEach(sf=>{{out+=`<line x1="${{X(sf)}}" y1="${{bottom}}" x2="${{X(sf)}}" y2="${{bottom+5}}" stroke="#aab8c7"/><text x="${{X(sf)}}" y="${{bottom+23}}" text-anchor="middle" font-size="12" fill="#526176">${{sf}}</text>`}});
  data.caps.forEach((cap,i)=>{{
    const color=colors[i%colors.length],label=cap+' Host Memory';
    const pts=xs.map(sf=>{{const r=S.find(r=>r.scale_factor===sf&&r.host_cap===cap);return `${{X(sf)}},${{Y(r.runtime_sum_seconds)}}`}}).join(' ');
    out+=`<polyline points="${{pts}}" fill="none" stroke="${{color}}" stroke-width="3"/>`;
    xs.forEach(sf=>{{const r=S.find(r=>r.scale_factor===sf&&r.host_cap===cap);out+=`<circle cx="${{X(sf)}}" cy="${{Y(r.runtime_sum_seconds)}}" r="4" fill="${{color}}"><title>SF${{sf}}, ${{label}}: ${{r.runtime_sum_seconds.toFixed(3)}} s</title></circle>`}});
    out+=`<text x="${{left+(i%4)*190}}" y="${{25+Math.floor(i/4)*22}}" fill="${{color}}" font-size="14">● ${{label}}</text>`;
  }});
  svg.innerHTML=out;
}}

function table(){{let sf=+document.getElementById('sf').value,cap=document.getElementById('cap').value,sort=document.getElementById('sort').value,rs=data.queries.filter(r=>r.scale_factor===sf&&r.host_cap===cap);rs.sort((a,b)=>sort==='query'?a.query-b.query:(b[sort]||0)-(a[sort]||0));document.getElementById('queryrows').innerHTML=rs.map(r=>`<tr><th>Q${{r.query}}</th><td>${{r.runtime_seconds.toFixed(3)}}</td><td>${{r.runtime_ratio_to_reference?.toFixed(2)??'—'}}×</td><td>${{r.host_placements}}</td><td>${{(r.host_logical_bytes/1073741824).toFixed(2)}}</td><td>${{r.disk_placements}}</td><td>${{(r.disk_logical_bytes/1073741824).toFixed(2)}}</td><td>${{r.disk_input_tasks}}</td></tr>`).join('')}}
['sf','cap','sort'].forEach(id=>document.getElementById(id).addEventListener('change',table));chart();table();
</script></body></html>'''


if __name__ == '__main__':
    main()
