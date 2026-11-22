#!/usr/bin/env python3
"""Lock a laboratory drill, save success/failure and publish atomic metrics."""
import fcntl
import json
import subprocess
import time
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent.parent
    artifacts = root / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    lock = (artifacts / 'drill.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('Restore drill is already running')
        return 75
    result_file = artifacts / 'drill.json'
    previous = json.loads(result_file.read_text()) if result_file.exists() else {}
    result = {'last_success_timestamp': previous.get('last_success_timestamp', 0),
              'started_timestamp': time.time(), 'success': False}
    started = time.monotonic()
    try:
        with (artifacts / 'drill.log').open('w') as log:
            operation = subprocess.run(['ansible-playbook', 'playbooks/drill.yml'], cwd=root,
                                       stdout=log, stderr=subprocess.STDOUT, timeout=21000)
        result['success'] = operation.returncode == 0
        result['returncode'] = operation.returncode
    except Exception as error:
        result['error_type'] = type(error).__name__
    result['duration_seconds'] = time.monotonic() - started
    result['finished_timestamp'] = time.time()
    if result['success']:
        result['last_success_timestamp'] = time.time()
    for name, content in [('drill.json', json.dumps(result, indent=2) + '\n'),
                          ('drill.prom', '\n'.join([
                              f"mongo_dr_drill_success {int(result['success'])}",
                              f"mongo_dr_drill_duration_seconds {result['duration_seconds']}",
                              f"mongo_dr_drill_last_success_timestamp_seconds {result['last_success_timestamp']}",
                              f"mongo_dr_drill_last_attempt_timestamp_seconds {result['started_timestamp']}", '']))]:
        temporary = artifacts / (name + '.tmp')
        temporary.write_text(content)
        temporary.chmod(0o644)
        temporary.replace(artifacts / name)
    print(json.dumps(result))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
