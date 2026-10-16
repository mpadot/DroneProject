"""Upload local testData CSV files to an S3 bucket."""

from __future__ import annotations

import os
from pathlib import Path
from pathlib import PurePosixPath

from data_pipeline import TESTDATA_DIR


def upload_testdata_to_s3() -> int:
    """Upload every CSV under testData to the configured S3 prefix.

    Credentials and region come from Boto3's standard AWS configuration
    chain. Set S3_BUCKET to the destination bucket name.
    """
    bucket = os.environ.get("S3_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("Set the S3_BUCKET environment variable to your bucket name.")

    prefix = os.environ.get("S3_PREFIX", "testData").strip("/")
    import boto3

    client = boto3.client("s3")
    files = sorted(TESTDATA_DIR.rglob("*.csv"))

    for file_path in files:
        relative_path = file_path.relative_to(TESTDATA_DIR).as_posix()
        key_path = PurePosixPath(prefix, relative_path) if prefix else PurePosixPath(relative_path)
        client.upload_file(str(file_path), bucket, key_path.as_posix())
        print(f"Uploaded {file_path.name} to s3://{bucket}/{key_path.as_posix()}")

    return len(files)
