#!/usr/bin/python3
"""Run PBM backup/cleanup and persist the outcome independently of monitoring."""
import argparse
import fcntl
import json
import os
import subprocess
import time
from pathlib import Path


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value) + '\n')
    temporary.chmod(0o644)
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--retention-days', type=int, required=True)
    args = parser.parse_args()
    if args.retention_days < 2:
        parser.error('Retention must be at least two days')
    state = Path(__file__).resolve().parent / 'state'
    with (state / 'backup.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another scheduled backup is active', flush=True)
            return 75
        started = time.monotonic()
        result = {'started_timestamp': time.time(), 'running': True}
        previous_path = state / 'backup.json'
        if previous_path.exists():
            previous = json.loads(previous_path.read_text())
            result['last_success_timestamp'] = previous.get('last_success_timestamp', 0)
        atomic_json(previous_path, result)
        rc = 1
        try:
            subprocess.run(['pbm', 'backup', '--type=logical', '--compression=s2', '--wait'],
                           check=True, timeout=13800)
            result['backup_success'] = True
            result['last_success_timestamp'] = time.time()
            result['backup_duration_seconds'] = time.monotonic() - started
            subprocess.run(['pbm', 'cleanup', f'--older-than={args.retention_days}d',
                            '--wait', '--yes'], check=True, timeout=300)
            result['cleanup_success'] = True
            rc = 0
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            # PBM diagnostics go to journal, never connection strings or config files.
            result['failed_step'] = 'cleanup' if result.get('backup_success') else 'backup'
            result['error_type'] = type(error).__name__
        finally:
            result.update(running=False, success=(rc == 0), finished_timestamp=time.time(),
                          duration_seconds=time.monotonic() - started)
            atomic_json(previous_path, result)
            print(json.dumps(result), flush=True)
        return rc


if __name__ == '__main__':
    raise SystemExit(main())
