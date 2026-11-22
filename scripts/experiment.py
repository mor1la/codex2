#!/usr/bin/python3
"""Generate a seeded workload, record PITR checkpoints, and verify a restore."""
import argparse
import hashlib
import json
import os
import math
import random
import re
import subprocess
import time
from archive_s3 import BackupArchive
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from bson import BSON, Binary
from pymongo import MongoClient


def pbm(*args):
    return subprocess.check_output(["pbm", *args], text=True, timeout=7200)


def utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def fingerprint(collection):
    digest = hashlib.sha256()
    count = 0
    size = 0
    for document in collection.find().sort("_id", 1):
        encoded = BSON.encode(document)
        digest.update(encoded)
        size += len(encoded)
        count += 1
    indexes = sorted(collection.index_information())
    return {"count": count, "bson_bytes": size, "sha256": digest.hexdigest(), "indexes": indexes}


def wait_coverage(target, timeout):
    deadline = time.monotonic() + timeout
    pattern = r"^\s*(\d{4}-\d\d-\d\dT[\d:]+)Z? - (\d{4}-\d\d-\d\dT[\d:]+)Z?\s*$"
    while time.monotonic() < deadline:
        listing = pbm("list")
        for start, end in re.findall(pattern, listing, re.MULTILINE):
            if start <= target < end:
                return end
        time.sleep(5)
    raise RuntimeError(f"PITR target not covered: {target}\n{listing}")


def payload(rng, size):
    return Binary(rng.randbytes(rng.randint(size // 2, size * 3 // 2)))


def mutate(collection, rng, args, first_id):
    ids = [item["_id"] for item in collection.find({}, {"_id": 1}).sort("_id", 1)]
    ops = Counter({"insert": 0, "update": 0, "delete": 0})
    start = time.monotonic()
    for step in range(int(args.rate * args.duration)):
        time.sleep(max(0, start + step / args.rate - time.monotonic()))
        op = rng.choices(("insert", "update", "delete"), args.weights)[0] if ids else "insert"
        if op == "insert":
            collection.insert_one({"_id": first_id, "project": rng.randrange(50), "revision": 0,
                                   "payload": payload(rng, args.payload)})
            ids.append(first_id)
            first_id += 1
        elif op == "update":
            collection.update_one({"_id": rng.choice(ids)}, {
                "$inc": {"revision": 1}, "$set": {"payload": payload(rng, args.payload)}})
        else:
            index = rng.randrange(len(ids))
            collection.delete_one({"_id": ids[index]})
            ids[index] = ids[-1]
            ids.pop()
        ops[op] += 1
    return first_id, dict(ops)


def seed_collection(collection, rng, args):
    """Fill to the requested BSON volume, or to the original document count."""
    target_bytes = int(args.target_gib * 1024 ** 3)
    inserted = 0
    generated_bytes = 0
    while generated_bytes < target_bytes if target_bytes else inserted < args.docs:
        batch = []
        while len(batch) < 500 and (generated_bytes < target_bytes if target_bytes else inserted < args.docs):
            document = {"_id": inserted, "project": rng.randrange(50), "revision": 0,
                        "payload": payload(rng, args.payload)}
            if target_bytes:
                generated_bytes += len(BSON.encode(document))
            batch.append(document)
            inserted += 1
        collection.insert_many(batch)
    return inserted


def run(client, collection, args):
    archive = BackupArchive()
    archive.check()
    if args.reset_benchmark_database:
        if collection.database.name != 'dr_benchmark' or client.admin.command('hello').get('setName') != 'drbench':
            raise RuntimeError('Benchmark reset requires database dr_benchmark in isolated replica set drbench')
        pbm('config', '--set', 'pitr.enabled=false')
        time.sleep(10)
        client.drop_database('dr_benchmark')
    if args.reset_drill_database:
        if collection.database.name != 'dr_scheduled_check':
            raise RuntimeError('Only the reserved dr_scheduled_check dataset can be reset')
        client.drop_database('dr_scheduled_check')
    if collection.database.name in client.list_database_names():
        raise RuntimeError("Use a fresh synthetic database name for each experiment")
    rng = random.Random(args.seed)
    seed_started = time.monotonic()
    collection.create_index("project")
    args.docs = seed_collection(collection, rng, args)
    base = fingerprint(collection)
    seed_seconds = round(time.monotonic() - seed_started, 2)
    print(json.dumps({'phase': 'seeded', 'documents': args.docs, 'bson_bytes': base['bson_bytes'],
                      'seed_seconds': seed_seconds}), flush=True)
    started = time.monotonic()
    output = json.loads(pbm("backup", "--type=logical", "--compression=s2", "--wait", "--out=json"))
    snapshot_seconds = round(time.monotonic() - started, 2)
    name = output["name"]
    base_files = archive.sizes(f"{name}/")
    base_files.update(archive.sizes(f"{name}.pbm.json"))
    if not base_files:
        raise RuntimeError(f"Missing files for snapshot {name}: {output}")
    if args.reset_benchmark_database:
        pbm('config', '--set', 'pitr.enabled=true')
    report = {"backend": "s3", "database": collection.database.name, "snapshot": name,
              "settings": {key: getattr(args, key) for key in
                           ("docs", "payload", "seed", "rate", "duration", "weights", "cycles")},
              "base": base, "snapshot_seconds": snapshot_seconds,
              "snapshot_bytes": sum(base_files.values()), "target_gib": args.target_gib,
              "seed_seconds": seed_seconds, "cycles": []}
    print(json.dumps({'phase': 'snapshot', 'snapshot': name, 'seconds': report['snapshot_seconds'],
                      'bytes': report['snapshot_bytes']}), flush=True)
    oplog = client.local["oplog.rs"]
    next_id = args.docs
    for _ in range(args.cycles):
        old_files = archive.sizes("pbmPitr/")
        before = oplog.find_one(sort=[("$natural", -1)])["ts"]
        started = time.monotonic()
        next_id, ops = mutate(collection, rng, args, next_id)
        workload_seconds = round(time.monotonic() - started, 2)
        oplog_bytes = 0
        after = None
        for entry in oplog.find({"ns": collection.full_name, "ts": {"$gt": before}}).sort("$natural", 1):
            oplog_bytes += len(BSON.encode(entry))
            after = entry["ts"]
        if after is None:
            raise RuntimeError("Workload produced no oplog entries")
        expected = fingerprint(collection)
        target = utc(after.time + 1)
        wait_start = time.monotonic()
        end = wait_coverage(target, args.coverage_timeout)
        new_files = archive.sizes("pbmPitr/")
        chunks = new_files.keys() - old_files.keys()
        if not chunks:
            raise RuntimeError("No new archived oplog chunks")
        report["cycles"].append({
            "operations": ops, "workload_seconds": workload_seconds,
            "oplog_bson_bytes": oplog_bytes,
            "archive_new_bytes": sum(new_files[key] for key in chunks),
            "archive_new_chunks": len(chunks), "target_utc": target,
            "coverage_end_utc": end, "coverage_wait_seconds": round(time.monotonic() - wait_start, 2),
            "expected": expected,
        })
        print(json.dumps(report["cycles"][-1]), flush=True)
    collection.insert_one({"_id": -1, "after_target": True})
    sentinel = oplog.find_one({"ns": collection.full_name, "o._id": -1}, sort=[("$natural", -1)])
    report["excluded_write_coverage_end_utc"] = wait_coverage(utc(sentinel["ts"].time + 1),
                                                              args.coverage_timeout)
    args.report.write_text(json.dumps(report, indent=2) + "\n")


def verify(collection, report):
    started = time.monotonic()
    validation = collection.database.command("validate", collection.name, full=True)
    actual = fingerprint(collection)
    expected = report["cycles"][-1]["expected"]
    if not validation.get("valid") or actual != expected or collection.find_one({"_id": -1}):
        raise RuntimeError(f"Restore validation failed. Expected {expected}, observed {actual}")
    print(json.dumps({"valid": True, "actual": actual, "after_target_absent": True,
                      "verification_seconds": round(time.monotonic() - started, 2)}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("run", "verify"))
    parser.add_argument("--database", default="dr_demo")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--docs", type=int, default=100000)
    parser.add_argument("--payload", type=int, default=4096)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--rate", type=int, default=50)
    parser.add_argument("--duration", type=int, default=15)
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--weights", type=int, nargs=3, default=(25, 60, 15))
    parser.add_argument("--coverage-timeout", type=int, default=240)
    parser.add_argument("--target-gib", type=float, default=0)
    reset = parser.add_mutually_exclusive_group()
    reset.add_argument("--reset-drill-database", action="store_true")
    reset.add_argument("--reset-benchmark-database", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"dr_[a-z0-9_]{1,40}", args.database):
        parser.error("Database name must start with dr_ and contain lowercase letters, digits or underscores")
    if min(args.docs, args.rate, args.duration, args.cycles, args.coverage_timeout) < 1:
        parser.error("Counts, rate and durations must be positive")
    if not math.isfinite(args.target_gib) or args.target_gib < 0:
        parser.error('Target GiB must be finite and nonnegative')
    if not 16 <= args.payload <= 1024 * 1024 or min(args.weights) < 0 or not sum(args.weights):
        parser.error("Invalid payload size or weights")
    with MongoClient(os.environ["PBM_MONGODB_URI"], directConnection=True, serverSelectionTimeoutMS=5000) as client:
        if args.mode == "run":
            run(client, client[args.database].events, args)
        else:
            report = json.loads(args.report.read_text())
            verify(client[report["database"]].events, report)


if __name__ == "__main__":
    main()
