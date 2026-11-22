#!/usr/bin/env python3
"""Persist a restore result and Prometheus textfile on the Ansible controller."""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path


def atomic_write(path, content):
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_report(path, event, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = json.loads(path.read_text()) if path.exists() else {}
    now = time.time()
    record = dict(data.get('metadata', {}))
    record.update({key: value for key, value in data.items() if key != 'metadata'})
    record['running'] = event == 'start'
    record['started_timestamp'] = now if event == 'start' else previous['started_timestamp']
    record['finished_timestamp'] = None if event == 'start' else now
    record['duration_seconds'] = max(0, now - record['started_timestamp'])
    record['last_success_timestamp'] = previous.get('last_success_timestamp', 0)
    record['last_completed'] = previous.get('last_completed')
    if event == 'start':
        record['success'] = None
        record['restore_success'] = None
        record['verification'] = None
    else:
        if record['success']:
            record['last_success_timestamp'] = now
        record['last_completed'] = {key: record.get(key) for key in (
            'success', 'restore_success', 'verification_status', 'duration_seconds',
            'finished_timestamp')}

    labels = '{target=' + json.dumps(record['target'], ensure_ascii=True) + '}'
    values = {
        'running': int(record['running']),
        'last_attempt_timestamp_seconds': record['started_timestamp'],
        'last_success_timestamp_seconds': record['last_success_timestamp'],
    }
    completed = record['last_completed']
    if completed:
        # Keep the last failure visible while another attempt is running.
        values['success'] = int(completed['success'])
        values['duration_seconds'] = completed['duration_seconds']
        if completed['restore_success'] is not None:
            values['database_success'] = int(completed['restore_success'])
        status = completed['verification_status']
        values['verification_performed'] = int(status in ('passed', 'failed'))
        if status in ('passed', 'failed'):
            values['verification_success'] = int(status == 'passed')
    metrics = ''.join(f'mongo_dr_restore_{name}{labels} {value}\n'
                      for name, value in sorted(values.items()))
    atomic_write(path, json.dumps(record, indent=2, ensure_ascii=False) + '\n')
    atomic_write(path.with_suffix('.prom'), metrics)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('event', choices=('start', 'finish'))
    parser.add_argument('--path', required=True, type=Path)
    args = parser.parse_args()
    write_report(args.path, args.event, json.load(sys.stdin))


if __name__ == '__main__':
    main()
