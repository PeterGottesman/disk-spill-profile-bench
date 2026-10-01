#!/usr/bin/env python3
"""Run a repeatable TPC-H host-capacity matrix with isolated artifacts."""
import argparse
import hashlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
VALID_CAP = re.compile(r'^[1-9][0-9]*(?:KiB|MiB|GiB|TiB)$')


def stamp():
    return datetime.now(timezone.utc).isoformat()


def run_output(command, **kw):
    return subprocess.check_output(command, text=True, **kw).strip()


def revision(path):
    try:
        return {'commit': run_output(['git', '-C', str(path), 'rev-parse', 'HEAD']),
                'dirty': bool(run_output(['git', '-C', str(path), 'status', '--porcelain']))}
    except (OSError, subprocess.CalledProcessError):
        return None


def config_values(config):
    command = f'source {shlex.quote(str(ROOT / "scripts/lib.sh"))}; load_config; export SIRIUS_ROOT DATA_ROOT PARQUET_ROOT SPILL_DIR SCALE_FACTORS PHASE3_ITERATIONS; env -0'
    env = os.environ.copy()
    env['BENCH_CONFIG_PATH'] = str(config)
    values = subprocess.check_output(['bash', '-c', command], env=env).split(b'\0')
    return {key.decode(): value.decode() for item in values if item for key, value in [item.split(b'=', 1)]}


def machine_info(data_root):
    info = {'hostname': os.uname().nodename, 'cpu_count': os.cpu_count(),
            'memory_kib': None, 'gpu': None, 'data_disk': None}
    try:
        info['memory_kib'] = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemTotal:')))
    except (OSError, StopIteration):
        pass
    try:
        info['cpu_model'] = next(line.split(':', 1)[1].strip() for line in run_output(['lscpu']).splitlines() if line.startswith('Model name:'))
    except (OSError, subprocess.CalledProcessError, StopIteration):
        info['cpu_model'] = platform.machine()
    try:
        info['gpu'] = run_output(['nvidia-smi', '--query-gpu=name,memory.total,driver_version', '--format=csv,noheader']).splitlines()
    except (OSError, subprocess.CalledProcessError):
        pass
    probe = data_root
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    info['data_disk'] = {'path': str(probe), 'total_bytes': usage.total, 'free_bytes': usage.free}
    return info


def save(manifest, path):
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(manifest, indent=2) + '\n')
    temporary.replace(path)


def completed_logs(run, scales, iterations):
    for sf in scales:
        path = Path(run['log_dir']) / f'phase3_sf{sf}.log'
        if not path.exists():
            raise ValueError(f'missing timing log: {path}')
        content = path.read_text(errors='replace')
        headers = re.findall(r'^=============== Q(\d+) iter (\d+) ===============$', content, re.M)
        if sorted((int(q), int(i)) for q, i in headers) != [(q, i) for q in range(1, 23) for i in range(1, iterations + 1)]:
            raise ValueError(f'incomplete query headers: {path}')
        if not re.search(rf'^=== Phase 3 SF{sf} end ', content, re.M):
            raise ValueError(f'incomplete log ending: {path}')
        if re.search(r'^Error:|^ERROR\b|FATAL', content, re.M | re.I):
            raise ValueError(f'query error in {path}')
        parts = re.split(r'^=============== Q\d+ iter \d+ ===============$', content, flags=re.M)[1:]
        if any(len(re.findall(r'Run Time \(s\): real ([0-9.]+)', part)) < 2 for part in parts):
            raise ValueError(f'missing query runtime: {path}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config.env')
    parser.add_argument('--host-caps', nargs='+', required=True, help='Largest or reference cap first, e.g. 196GiB 8GiB 1GiB')
    parser.add_argument('--scale-factors', nargs='+', type=int)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--iterations', type=int, default=2)
    parser.add_argument('--id', default='matrix_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--generate', action='store_true', help='Generate missing Parquet datasets')
    parser.add_argument('--jobs', type=int, default=8)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.id):
        parser.error('--id must contain only letters, digits, underscores, or hyphens')
    if len(set(args.host_caps)) != len(args.host_caps) or any(not VALID_CAP.fullmatch(c) for c in args.host_caps):
        parser.error('--host-caps must be unique positive KiB/MiB/GiB/TiB values')
    if args.repetitions < 1 or args.iterations < 1 or args.jobs < 1:
        parser.error('repetitions, iterations, and jobs must be positive')
    config = args.config.resolve()
    if not config.is_file():
        parser.error(f'config file missing: {config}')
    values = config_values(config)
    sirius = Path(values['SIRIUS_ROOT']).resolve()
    parquet = Path(values['PARQUET_ROOT']).resolve()
    scales = args.scale_factors or [int(s) for s in values['SCALE_FACTORS'].split()]
    if not scales or min(scales) < 1 or len(set(scales)) != len(scales):
        parser.error('scale factors must be unique positive integers')
    output = (args.output or Path(values['DATA_ROOT']) / 'experiments' / args.id).resolve()
    manifest_path = output / 'manifest.json'
    runs = []
    for rep in range(1, args.repetitions + 1):
        for cap in args.host_caps:
            run_id = f'{args.id}_r{rep}_{cap.lower()}'
            base = output / 'runs' / run_id
            runs.append({'id': run_id, 'repetition': rep, 'host_cap': cap, 'path': str(base),
                         'config': str(base / 'config.env'), 'yaml': str(base / 'sirius.yaml'),
                         'telemetry_dir': str(base / 'telemetry'), 'log_dir': str(base / 'logs' / run_id),
                         'metrics': str(base / 'metrics.json'), 'console': str(base / 'console.log'), 'status': 'pending'})
    if args.dry_run:
        print(json.dumps({'output': str(output), 'sirius': str(sirius), 'parquet_root': str(parquet),
                          'scales': scales, 'runs': [{'id': r['id'], 'host_cap': r['host_cap']} for r in runs]}, indent=2))
        return
    for item in [sirius / 'build/release/duckdb', sirius / 'test/tpch_performance/generate_tpch_data.sh']:
        if not item.is_file():
            parser.error(f'missing Sirius prerequisite: {item}')
    if shutil.which('pixi') is None:
        parser.error('pixi is required in PATH')
    config_sha256 = hashlib.sha256(config.read_bytes()).hexdigest()
    if manifest_path.exists():
        if not args.resume:
            parser.error(f'{manifest_path} exists; choose another --id or use --resume')
        manifest = json.loads(manifest_path.read_text())
        if manifest['scale_factors'] != scales or manifest['host_caps'] != args.host_caps or manifest['iterations'] != args.iterations or manifest['repetitions'] != args.repetitions or Path(manifest['config_source']) != config or manifest.get('config_sha256') != config_sha256:
            parser.error('resume parameters differ from the manifest')
        runs = manifest['runs']
    else:
        if output.exists() and any(output.iterdir()):
            parser.error(f'output directory is not empty: {output}')
        output.mkdir(parents=True, exist_ok=True)
        manifest = {'schema_version': 1, 'experiment_id': args.id, 'created_at': stamp(),
                    'config_source': str(config), 'config_sha256': config_sha256, 'sirius_root': str(sirius), 'parquet_root': str(parquet),
                    'scale_factors': scales, 'host_caps': args.host_caps, 'reference_host_cap': args.host_caps[0],
                    'repetitions': args.repetitions, 'iterations': args.iterations, 'queries': list(range(1, 23)),
                    'machine': machine_info(Path(values['DATA_ROOT'])), 'sirius_revision': revision(sirius),
                    'harness_revision': revision(ROOT), 'datasets': [], 'runs': runs}
        save(manifest, manifest_path)
    print(f'Manifest: {manifest_path}', flush=True)
    for sf in scales:
        dataset = parquet / f'sf{sf}'
        if not dataset.exists():
            if not args.generate:
                raise SystemExit(f'Missing {dataset}; use --generate to create it')
            print(f'Generating SF{sf} at {dataset}', flush=True)
            dataset.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['pixi', 'run', '--', 'bash', 'test/tpch_performance/generate_tpch_data.sh', str(sf), '--format', 'parquet', '--output', str(dataset), '--jobs', str(args.jobs)], cwd=sirius, check=True)
        validated = subprocess.check_output(['pixi', 'run', '--', 'python', str(ROOT / 'scripts/validate-tpch-dataset.py'), str(dataset)], cwd=sirius, text=True)
        record = json.loads(validated.strip())
        if int(record['scale_factor']) != sf:
            raise ValueError(f'dataset scale mismatch: {dataset}')
        manifest['datasets'] = [d for d in manifest['datasets'] if d['scale_factor'] != sf] + [record]
        save(manifest, manifest_path)
        print(f'Validated SF{sf}: {record["parquet_gib"]} GiB', flush=True)
    for entry in runs:
        if entry['status'] == 'complete':
            completed_logs(entry, scales, args.iterations)
            if not Path(entry['metrics']).is_file():
                raise ValueError(f'missing completed metrics: {entry["metrics"]}')
            print(f'Skipping complete {entry["id"]}', flush=True)
            continue
        base = Path(entry['path'])
        if args.resume and entry['status'] != 'pending' and Path(entry['telemetry_dir']).exists() and any(Path(entry['telemetry_dir']).iterdir()):
            raise ValueError(f'incomplete run has existing telemetry; choose a new --id to avoid mixing attempts: {entry["id"]}')
        base.mkdir(parents=True, exist_ok=True)
        for name in ['telemetry', 'spill', 'logs']:
            (base / name).mkdir(exist_ok=True)
        overrides = {'SIRIUS_ROOT': str(sirius), 'DATA_ROOT': str(base), 'PARQUET_ROOT': str(parquet),
                     'SPILL_DIR': str(base / 'spill'), 'TELEMETRY_DIR': entry['telemetry_dir'],
                     'LOG_DIR': str(base / 'logs'), 'YAML_PATH': entry['yaml'], 'SCALE_FACTORS': ' '.join(map(str, scales)),
                     'PHASE3_ITERATIONS': str(args.iterations), 'HOST_CAPACITY': entry['host_cap'], 'QUENT_EXPORTER': 'ndjson'}
        # Freeze resolved config values so later edits cannot change this run's settings.
        key_pattern = re.compile(r'^\s*([A-Z][A-Z0-9_]*)=', re.M)
        keys = set(key_pattern.findall(config.read_text())) | set(key_pattern.findall((ROOT / 'config.env.example').read_text()))
        frozen = {key: values[key] for key in keys if key in values}
        frozen.update(overrides)
        snapshot = '# Resolved config snapshot generated by run-matrix.py\n'
        snapshot += ''.join(f'{key}={shlex.quote(value)}\n' for key, value in sorted(frozen.items()))
        Path(entry['config']).write_text(snapshot)
        env = os.environ.copy()
        env.update({'BENCH_CONFIG_PATH': entry['config'], 'BENCH_RUN_ID': entry['id']})
        entry['status'] = 'running'
        entry['started_at'] = stamp()
        save(manifest, manifest_path)
        print(f'Running {entry["id"]} ({entry["host_cap"]}, repetition {entry["repetition"]})', flush=True)
        try:
            with Path(entry['console']).open('w') as console:
                subprocess.run(['bash', str(ROOT / 'scripts/run-phase3.sh')], cwd=ROOT, env=env, stdout=console, stderr=subprocess.STDOUT, check=True)
            completed_logs(entry, scales, args.iterations)
            subprocess.run([sys.executable, str(ROOT / 'scripts/analyze-telemetry-ndjson.py'), entry['telemetry_dir'], '--output', entry['metrics']], check=True)
            metrics = json.loads(Path(entry['metrics']).read_text())
            expected = {(q, i) for q in range(1, 23) for i in range(1, args.iterations + 1)}
            for sf in scales:
                rows = [r for r in metrics if r['label'].startswith(f'full_sf{sf}_run_{entry["id"]}_')]
                if {(r['query'], r['iteration']) for r in rows} != expected or len(rows) != len(expected):
                    raise ValueError(f'incomplete telemetry for SF{sf}: {entry["id"]}')
            entry['status'] = 'complete'
        except Exception as exc:
            entry['status'] = 'failed'
            entry['error'] = str(exc)
            raise
        finally:
            entry['finished_at'] = stamp()
            save(manifest, manifest_path)
    print(f'Complete: {manifest_path}', flush=True)


if __name__ == '__main__':
    main()
