#!/usr/bin/python3
"""Initialize only the dedicated local replica set and its first user."""
import os
import sys
import time
from urllib.parse import urlsplit

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure


replica, port = sys.argv[1:]
uri = os.environ["PBM_MONGODB_URI"]
options = {"directConnection": True, "serverSelectionTimeoutMS": 1500}
local = MongoClient(f"mongodb://127.0.0.1:{port}", **options)
auth = MongoClient(uri, **options)
changed = False

for _ in range(40):
    try:
        hello = local.admin.command("hello")
        if not hello.get("setName"):
            try:
                local.admin.command("replSetInitiate", {
                    "_id": replica, "members": [{"_id": 0, "host": f"localhost:{port}"}]
                })
                changed = True
            except OperationFailure as error:
                if error.code != 23:
                    raise
        elif hello["setName"] != replica:
            raise RuntimeError("Unexpected replica set name")
        elif hello.get("isWritablePrimary"):
            try:
                auth.admin.command("connectionStatus")
                print("initialized" if changed else "ready")
                break
            except OperationFailure as error:
                if error.code != 18:
                    raise
                try:
                    local.admin.command("createUser", "dradmin", pwd=urlsplit(uri).password,
                                        roles=[{"role": "root", "db": "admin"}])
                except OperationFailure as create_error:
                    if create_error.code == 13:
                        raise RuntimeError("Existing instance rejects configured credentials") from None
                    raise
                changed = True
    except ConnectionFailure:
        pass
    time.sleep(1)
else:
    raise RuntimeError("Replica set did not become ready")
