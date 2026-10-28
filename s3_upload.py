"""Upload CSV files created by a flight session to an S3 bucket."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from data_pipeline import TESTDATA_DIR


def upload_csv_files_to_s3(csv_files: list[str | os.PathLike[str]]) -> int:
    """Upload the supplied CSV files to the configured S3 prefix.

    Credentials and region come from Boto3's standard AWS configuration
    chain. Set S3_BUCKET to the destination bucket name. S3_PREFIX defaults
    to ``testData``.
    """
    files = [Path(file_path) for file_path in csv_files]
    if not files:
        print("No new CSV files to upload.")
        return 0

    bucket = os.environ.get("S3_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("Set the S3_BUCKET environment variable to your bucket name.")

    prefix = os.environ.get("S3_PREFIX", "testData").strip("/")
    import boto3

    client = boto3.client("s3")
    for file_path in files:
        if not file_path.is_file():
            raise FileNotFoundError(f"CSV file does not exist: {file_path}")
        try:
            relative_path = file_path.resolve().relative_to(TESTDATA_DIR.resolve()).as_posix()
        except ValueError:
            relative_path = file_path.name
        key_path = PurePosixPath(prefix, relative_path) if prefix else PurePosixPath(relative_path)
        client.upload_file(str(file_path), bucket, key_path.as_posix())
        print(f"Uploaded {file_path.name} to s3://{bucket}/{key_path.as_posix()}")

    return len(files)


def upload_testdata_to_s3() -> int:
    """Upload all CSV files under testData (useful for manual bulk uploads)."""
    return upload_csv_files_to_s3(sorted(TESTDATA_DIR.rglob("*.csv")))
