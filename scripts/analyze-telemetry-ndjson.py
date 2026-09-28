#!/usr/bin/env python3
"""Summarize per-query HOST and DISK batch-placement transitions in Quent NDJSON."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re


def records(session, kind):
    for path in sorted((session / kind).glob('*.ndjson')):
        with path.open() as source:
            for line in source:
                yield json.loads(line)


def state(event):
    value = event['data'].get('state')
    if isinstance(value, dict):
        return next(iter(value.items()))
    return None, None


def analyze_session(session):
    labels = {}
    for event in records(session, 'query'):
        name, data = state(event)
        if name == 'Init':
            labels[event['id']] = data['instance_name']
    if not labels:
        return []

    parent_plans = {}
    for event in records(session, 'plan'):
        data = event['data'].get('Declaration')
        if data:
            parent_plans[event['id']] = data['parent']

    def plan_query(plan_id):
        seen = set()
        while plan_id:
            if plan_id in seen:
                raise ValueError(f'plan cycle in {session}')
            seen.add(plan_id)
            parent = parent_plans[plan_id]
            if parent.get('query_id'):
                return parent['query_id']
            plan_id = parent.get('plan_id')
        raise ValueError(f'plan with no query in {session}')

    pipeline_query = {}
    for event in records(session, 'operator'):
        data = event['data'].get('Declaration')
        if data:
            pipeline_query[event['id']] = plan_query(data['plan_id'])

    tier_names = {}
    capacities = {}
    for event in records(session, 'memory_tier'):
        name, data = state(event)
        if name == 'MemoryTierInitializing':
            tier_names[event['id']] = data['instance_name']
        elif name == 'MemoryTierOperating':
            capacities[event['id']] = data.get('capacity_bytes')

    placement_query = {}
    batch_ids = {}
    for event in records(session, 'batch_placement'):
        name, data = state(event)
        if name == 'BatchRegistered':
            placement_query[event['id']] = pipeline_query[data['pipeline_uuid']]
            batch_ids[event['id']] = data['batch_id']

    rows = {qid: {
        'query_id': qid, 'label': label, 'session': session.name,
        'transition_counts': Counter(), 'transition_logical_bytes': Counter(),
        'unique_batches_in_host': {}, 'unique_batches_in_disk': {},
        'tasks_prepared_with_host_input': 0,
        'tasks_prepared_with_disk_input': 0,
    } for qid, label in labels.items()}
    last_tier = {}
    for event in records(session, 'batch_placement'):
        name, data = state(event)
        if not isinstance(data, dict) or 'tier' not in data:
            continue
        placement = event['id']
        qid = placement_query.get(placement)
        if qid is None:
            raise ValueError(f'placement missing query mapping: {placement}')
        tier = data['tier']
        target = tier_names[tier['resource_id']]
        previous = last_tier.get(placement)
        if previous != target:
            key = f'{previous or "NEW"}->{target}'
            size = tier['capacity']['capacity_bytes']
            row = rows[qid]
            row['transition_counts'][key] += 1
            row['transition_logical_bytes'][key] += size
            if target in ('HOST', 'DISK'):
                unique = row[f'unique_batches_in_{target.lower()}']
                batch_id = batch_ids[placement]
                unique[batch_id] = max(unique.get(batch_id, 0), size)
        last_tier[placement] = target

    task_query = {}
    for event in records(session, 'task'):
        name, data = state(event)
        if name == 'Created':
            task_query[event['id']] = pipeline_query[data['pipeline_uuid']]
        elif name == 'Preparing':
            qid = task_query.get(event['id'])
            if qid is None:
                raise ValueError(f'task missing query mapping: {event["id"]}')
            origin = data.get('origin_tier', '')
            rows[qid]['tasks_prepared_with_host_input'] += 'HOST' in origin
            rows[qid]['tasks_prepared_with_disk_input'] += 'DISK' in origin

    result = []
    for qid, row in rows.items():
        match = re.search(r'_tpch_q(\d+)_iter(\d+)$', row['label'])
        if not match:
            continue
        row['query'] = int(match[1])
        row['iteration'] = int(match[2])
        for tier in ('host', 'disk'):
            unique = row.pop(f'unique_batches_in_{tier}')
            row[f'unique_batches_in_{tier}'] = len(unique)
            row[f'unique_batch_logical_bytes_in_{tier}'] = sum(unique.values())
        row['transition_counts'] = dict(row['transition_counts'])
        row['transition_logical_bytes'] = dict(row['transition_logical_bytes'])
        row['tier_capacities_bytes'] = {tier_names[k]: v for k, v in capacities.items()}
        result.append(row)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('telemetry_dir', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    rows = []
    for session in sorted(args.telemetry_dir.iterdir()):
        if session.is_dir():
            rows.extend(analyze_session(session))
    rows.sort(key=lambda r: (r['session'], r['query'], r['iteration']))
    payload = json.dumps(rows, indent=2) + '\n'
    if args.output:
        args.output.write_text(payload)
    else:
        print(payload, end='')


if __name__ == '__main__':
    main()
