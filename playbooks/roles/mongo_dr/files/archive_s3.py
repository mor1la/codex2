#!/usr/bin/python3
"""List object sizes in PBM's S3 prefix using the managed PBM configuration."""
from pathlib import Path

import boto3
import yaml
from botocore.config import Config


class BackupArchive:
    def __init__(self):
        config = yaml.safe_load(Path(__file__).with_name("pbm.yml").read_text())
        storage = config["storage"]["s3"]
        credentials = storage["credentials"]
        self.bucket = storage["bucket"]
        self.prefix = storage.get("prefix", "").strip("/")
        self.client = boto3.client(
            "s3", endpoint_url=storage["endpointUrl"], region_name=storage["region"],
            aws_access_key_id=credentials["access-key-id"],
            aws_secret_access_key=credentials["secret-access-key"],
            config=Config(connect_timeout=4, read_timeout=10,
                          retries={"total_max_attempts": 2},
                          s3={"addressing_style": "path"}),
        )

    def check(self):
        self.client.list_objects_v2(Bucket=self.bucket, Prefix=self.prefix, MaxKeys=1)

    def sizes(self, prefix):
        key_prefix = "/".join(part for part in (self.prefix, prefix) if part)
        pages = self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=key_prefix)
        return {item["Key"]: item["Size"]
                for page in pages for item in page.get("Contents", [])}


if __name__ == "__main__":
    BackupArchive().check()
