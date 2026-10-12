"""Upload a recorded flight CSV to S3 and verify the object landed.

Works from your laptop (Learner Lab keys) or from an EC2 instance that has
an IAM role / the same environment variables.

Required environment variables:
  AWS_ACCESS_KEY_ID
  AWS_SECRET_ACCESS_KEY
  AWS_DEFAULT_REGION          (Learner Lab is usually us-east-1)
  S3_BUCKET                   (bucket name, no s3:// prefix)

Optional:
  S3_PREFIX                   default: flights/
  AWS_SESSION_TOKEN           needed for temporary academy credentials

Create the bucket once (Learner Lab console, or):
  python s3_upload.py --ensure-bucket

Upload a CSV:
  python s3_upload.py data/flights/flight_....csv
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "boto3 is not installed. Run:  pip install boto3"
    ) from exc


DEFAULT_REGION = "us-east-1"
DEFAULT_PREFIX = "flights/"
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = (1, 2, 4, 8)
FAILED_LOG = Path(__file__).resolve().parent / "data" / "failed_uploads.jsonl"


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise RuntimeError(
            f"Missing {name}. Set it in your environment or a .env file "
            "before uploading."
        )
    return value


def load_dotenv(path: str | os.PathLike[str] | None = None) -> None:
    """Tiny .env loader so we do not need the python-dotenv package."""
    env_path = Path(path or Path(__file__).resolve().parent / ".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def s3_client():
    load_dotenv()
    region = os.environ.get("AWS_DEFAULT_REGION", DEFAULT_REGION)
    kwargs: dict[str, Any] = {"region_name": region}
    # If keys are in the environment, boto3 picks them up automatically.
    # On EC2, omit keys and the instance role is used instead.
    return boto3.client("s3", **kwargs)


def bucket_name() -> str:
    load_dotenv()
    return _env("S3_BUCKET")


def object_key_for(local_path: Path, prefix: str | None = None) -> str:
    load_dotenv()
    prefix = prefix if prefix is not None else os.environ.get("S3_PREFIX", DEFAULT_PREFIX)
    prefix = prefix.strip("/")
    stamp = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    if prefix:
        return f"{prefix}/{stamp}/{local_path.name}"
    return f"{stamp}/{local_path.name}"


def ensure_bucket(client=None, name: str | None = None) -> str:
    """Create the S3 bucket if it does not already exist."""
    client = client or s3_client()
    name = name or bucket_name()
    region = os.environ.get("AWS_DEFAULT_REGION", DEFAULT_REGION)
    try:
        client.head_bucket(Bucket=name)
        print(f"Bucket already exists: {name}")
        return name
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in {"404", "NoSuchBucket", "403", "404 Not Found"}:
            # 403 can mean the bucket exists but we cannot Head it; try create anyway.
            if code not in {"403"}:
                raise

    params: dict[str, Any] = {"Bucket": name}
    if region != "us-east-1":
        params["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        client.create_bucket(**params)
        print(f"Created bucket: {name}")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
            raise
        print(f"Bucket already present: {name}")
    return name


def _md5_hex(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_failure(local_path: Path, key: str, error: str) -> None:
    FAILED_LOG.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "local_path": str(local_path),
        "s3_key": key,
        "error": error,
    }
    with FAILED_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")


def verify_upload(client, bucket: str, key: str, local_path: Path) -> None:
    """Confirm the object exists and matches the local file size (and MD5 when possible)."""
    head = client.head_object(Bucket=bucket, Key=key)
    local_size = local_path.stat().st_size
    remote_size = int(head["ContentLength"])
    if remote_size != local_size:
        raise RuntimeError(
            f"Size mismatch for s3://{bucket}/{key}: "
            f"local={local_size} remote={remote_size}"
        )

    etag = head.get("ETag", "").strip('"')
    local_md5 = _md5_hex(local_path)
    # Multipart ETags contain a dash and are not a plain MD5.
    if etag and "-" not in etag and etag.lower() != local_md5.lower():
        raise RuntimeError(
            f"ETag/MD5 mismatch for s3://{bucket}/{key}: "
            f"local={local_md5} etag={etag}"
        )
    print(
        f"Verified s3://{bucket}/{key} "
        f"({remote_size} bytes, etag={etag or 'n/a'})"
    )


def upload_flight_csv(
    local_path: str | os.PathLike[str],
    key: str | None = None,
    bucket: str | None = None,
) -> str:
    """Upload one CSV with retries. Returns the s3:// URI on success."""
    path = Path(local_path)
    if not path.is_file():
        raise FileNotFoundError(f"CSV not found: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"Refusing to upload empty file: {path}")

    load_dotenv()
    client = s3_client()
    bucket = bucket or bucket_name()
    key = key or object_key_for(path)

    last_error: Exception | None = None
    for attempt, delay in enumerate(BACKOFF_SECONDS[:MAX_ATTEMPTS], start=1):
        try:
            extra_args = {
                "ContentType": "text/csv",
                "Metadata": {
                    "source": "codrone-edu",
                    "original-filename": path.name,
                },
            }
            client.upload_file(
                Filename=str(path),
                Bucket=bucket,
                Key=key,
                ExtraArgs=extra_args,
            )
            verify_upload(client, bucket, key, path)
            uri = f"s3://{bucket}/{key}"
            print(f"Upload complete: {uri}")
            return uri
        except (BotoCoreError, ClientError, RuntimeError, OSError) as exc:
            last_error = exc
            print(f"[s3_upload] attempt {attempt}/{MAX_ATTEMPTS} failed: {exc}")
            if attempt < MAX_ATTEMPTS:
                time.sleep(delay)

    _record_failure(path, key, str(last_error))
    raise RuntimeError(
        f"Upload failed after {MAX_ATTEMPTS} attempts. "
        f"Logged to {FAILED_LOG}. Last error: {last_error}"
    ) from last_error


def retry_failed_uploads() -> list[str]:
    """Re-upload anything recorded in data/failed_uploads.jsonl."""
    if not FAILED_LOG.exists():
        print("No failed-upload log found.")
        return []

    lines = FAILED_LOG.read_text(encoding="utf-8").splitlines()
    remaining: list[str] = []
    uploaded: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        record = json.loads(line)
        local = Path(record["local_path"])
        key = record.get("s3_key")
        try:
            uri = upload_flight_csv(local, key=key)
            uploaded.append(uri)
        except Exception as exc:
            print(f"Still failing {local}: {exc}")
            remaining.append(line)

    FAILED_LOG.write_text("\n".join(remaining) + ("\n" if remaining else ""), encoding="utf-8")
    return uploaded


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Upload flight CSV files to S3.")
    parser.add_argument("csv_path", nargs="?", help="Local CSV to upload.")
    parser.add_argument("--ensure-bucket", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()

    if args.ensure_bucket:
        ensure_bucket()
    if args.retry_failed:
        retry_failed_uploads()
    if args.csv_path:
        upload_flight_csv(args.csv_path)
    if not (args.ensure_bucket or args.retry_failed or args.csv_path):
        parser.print_help()
