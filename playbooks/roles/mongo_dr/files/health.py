#!/usr/bin/python3
"""Emit backup health metrics to the systemd journal and fail on SLO breaches."""
import argparse
import json
import subprocess
import time
from archive_s3 import BackupArchive

parser = argparse.ArgumentParser()
parser.add_argument("--max-lag", type=int, default=180)
args = parser.parse_args()
BackupArchive().check()

listing = json.loads(subprocess.check_output(
    ["pbm", "list", "--out=json"], text=True, timeout=20
))
pitr = listing["pitr"]
snapshots = listing["snapshots"] or []
ranges = [item["range"] for item in pitr["ranges"] or []]
latest_point = max([item["end"] for item in ranges]
                   + [item["restoreTo"] for item in snapshots], default=0)
lag = max(0, int(time.time()) - latest_point) if latest_point else None
healthy = pitr["on"] and lag is not None and lag <= args.max_lag
print(json.dumps({"healthy": healthy, "pitr_enabled": pitr["on"],
                  "restore_point_age_seconds": lag, "completed_snapshots": len(snapshots)}))
raise SystemExit(0 if healthy else 1)
