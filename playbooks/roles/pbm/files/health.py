#!/usr/bin/python3
"""Atomic Prometheus textfile and JSON health; no exporter/server dependency."""
import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

from pymongo import MongoClient
from archive_s3 import BackupArchive


def pbm_json(*args):
    output = subprocess.run(['pbm', *args, '--out=json'], check=True, capture_output=True,
                            text=True, timeout=15)
    return json.loads(output.stdout)


def duration_seconds(value):
    units = {'h': 3600, 'm': 60, 's': 1, 'ms': .001, 'us': .000001, 'ns': .000000001}
    return sum(float(n) * units[u] for n, u in
               re.findall(r'(\d+(?:\.\d+)?)(ns|us|ms|h|m|s)', value))


def collect(agent_service):
    metrics = {'collector_timestamp_seconds': time.time()}
    errors = []
    try:
        with MongoClient(os.environ['PBM_MONGODB_URI'], directConnection=True,
                         serverSelectionTimeoutMS=3000, connectTimeoutMS=3000,
                         socketTimeoutMS=3000) as client:
            client.admin.command('connectionStatus')
        metrics['mongodb_up'] = 1
    except Exception:
        metrics['mongodb_up'] = 0
        errors.append('mongodb')
    metrics['pbm_agent_up'] = int(subprocess.run(
        ['systemctl', 'is-active', '--quiet', agent_service], timeout=5).returncode == 0)
    try:
        archive = BackupArchive()
        archive.check()
        metrics['s3_up'] = 1
    except Exception:
        metrics['s3_up'] = 0
        errors.append('s3')
    try:
        listing = pbm_json('list')
        snapshots = [s for s in listing.get('snapshots') or [] if s['status'] == 'done']
        full = [s for s in snapshots if s.get('type') == 'logical']
        ranges = [r['range'] for r in listing.get('pitr', {}).get('ranges') or []]
        metrics['latest_full_timestamp_seconds'] = max((s['restoreTo'] for s in full), default=0)
        metrics['latest_restore_point_timestamp_seconds'] = max(
            [r['end'] for r in ranges] + [s['restoreTo'] for s in snapshots], default=0)
        metrics['pitr_enabled'] = int(listing['pitr']['on'])
        metrics['completed_snapshots'] = len(snapshots)
        metrics['last_full_duration_seconds'] = duration_seconds(full[-1].get('duration', '0s')) if full else 0
        status = pbm_json('status')
        catalogue = status.get('backups', {})
        known = catalogue.get('snapshot') or []
        metrics['archive_bytes'] = sum(s.get('size', 0) for s in known if s['status'] == 'done') + catalogue.get('pitrChunks', {}).get('size', 0)
        metrics['pbm_cluster_healthy'] = int(bool(status.get('cluster')) and all(
            n.get('ok', False) for rs in status['cluster'] for n in rs.get('nodes', [])))
        # Include failed backups initiated outside the scheduled wrapper.
        metrics['catalog_backup_success'] = int(not known or known[0]['status'] not in ('error', 'cancelled', 'canceled'))
        metrics['pbm_query_success'] = 1
    except Exception:
        metrics['pbm_query_success'] = 0
        errors.append('pbm')
    state = Path(__file__).resolve().parent / 'state'
    result_file = state / 'backup.json'
    if result_file.exists():
        result = json.loads(result_file.read_text())
        metrics['backup_running'] = int(result.get('running', False))
        metrics['backup_started_timestamp_seconds'] = result.get('started_timestamp', 0)
        if 'success' in result:
            metrics['backup_success'] = int(result['success'])
            metrics['backup_duration_seconds'] = result['duration_seconds']
        metrics['backup_last_success_timestamp_seconds'] = result.get('last_success_timestamp', 0)
    metrics['collector_success'] = int(not errors)
    return metrics, errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--agent-service', required=True)
    parser.add_argument('--max-lag', type=int, default=180)
    parser.add_argument('--require-pitr', '--source', dest='require_pitr', action='store_true')
    args = parser.parse_args()
    state = Path(__file__).resolve().parent / 'state'
    try:
        metrics, errors = collect(args.agent_service)
    except Exception as error:
        # Preserve a visible failed heartbeat even on an unexpected parsing error.
        metrics = {'collector_timestamp_seconds': time.time(), 'collector_success': 0}
        errors = [type(error).__name__]
    point = metrics.get('latest_restore_point_timestamp_seconds', 0)
    healthy = not errors and bool(metrics.get('pbm_agent_up')) and bool(metrics.get('pbm_cluster_healthy'))
    if args.require_pitr:
        healthy = healthy and bool(metrics.get('pitr_enabled')) and bool(point) and time.time() - point <= args.max_lag
    report = dict(healthy=bool(healthy), errors=errors, metrics=metrics)
    for name, content in [('health.json', json.dumps(report) + '\n'),
                          ('metrics.prom', ''.join(f'mongo_dr_{k} {v}\n' for k, v in sorted(metrics.items())))]:
        temporary = state / (name + '.tmp')
        temporary.write_text(content)
        temporary.chmod(0o644)
        temporary.replace(state / name)
    print(json.dumps(report))
    return 0 if healthy else 1


if __name__ == '__main__':
    raise SystemExit(main())
