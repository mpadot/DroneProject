"""Create the S3 bucket and optional IAM upload users.

EC2 is not created here. The milestone only needs S3; teammates can upload
from the PH 517 laptop with IAM (or Learner Lab) keys.

Usage:
  python aws_setup.py --bucket your-unique-bucket-name
  python aws_setup.py --bucket your-unique-bucket-name --users alice bob

Do not commit access keys. Share each user's key privately once.
AWS Academy Learner Lab often blocks CreateUser; if that happens, share
one lab session's keys with the team instead (they expire when the lab ends).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from s3_upload import DEFAULT_REGION, ensure_bucket, load_dotenv, s3_client

POLICY_PATH = Path(__file__).resolve().parent / "iam" / "uploader-policy.json"


def _iam_client():
    import boto3

    load_dotenv()
    import os

    region = os.environ.get("AWS_DEFAULT_REGION", DEFAULT_REGION)
    return boto3.client("iam", region_name=region)


def _safe_username(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9+=,.@_-]", "-", name.strip())
    if not cleaned:
        raise ValueError(f"Invalid IAM user name: {name!r}")
    return cleaned[:64]


def create_uploader_user(username: str, bucket: str) -> dict[str, str]:
    iam = _iam_client()
    username = _safe_username(username)
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    policy_json = json.dumps(policy).replace("REPLACE_BUCKET_NAME", bucket)

    try:
        iam.create_user(
            UserName=username,
            Tags=[{"Key": "project", "Value": "DroneProject"}],
        )
        print(f"Created IAM user: {username}")
    except iam.exceptions.EntityAlreadyExistsException:
        print(f"IAM user already exists: {username}")

    iam.put_user_policy(
        UserName=username,
        PolicyName="DroneFlightUploader",
        PolicyDocument=policy_json,
    )
    key = iam.create_access_key(UserName=username)["AccessKey"]
    return {
        "UserName": username,
        "AWS_ACCESS_KEY_ID": key["AccessKeyId"],
        "AWS_SECRET_ACCESS_KEY": key["SecretAccessKey"],
        "AWS_DEFAULT_REGION": DEFAULT_REGION,
        "S3_BUCKET": bucket,
        "S3_PREFIX": "flights/",
    }


def print_env_block(creds: dict[str, str]) -> None:
    print("\n--- give this teammate a private .env (do not commit, do not paste in git) ---")
    print(f"AWS_ACCESS_KEY_ID={creds['AWS_ACCESS_KEY_ID']}")
    print(f"AWS_SECRET_ACCESS_KEY={creds['AWS_SECRET_ACCESS_KEY']}")
    print(f"AWS_DEFAULT_REGION={creds['AWS_DEFAULT_REGION']}")
    print(f"S3_BUCKET={creds['S3_BUCKET']}")
    print(f"S3_PREFIX={creds['S3_PREFIX']}")
    print("--------------------------------------------------------------------------------\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create S3 bucket and IAM uploaders.")
    parser.add_argument("--bucket", required=True, help="Globally unique S3 bucket name.")
    parser.add_argument(
        "--users",
        nargs="*",
        default=[],
        help="IAM user names for teammates who only need S3 upload.",
    )
    args = parser.parse_args()

    load_dotenv()
    import os

    os.environ["S3_BUCKET"] = args.bucket
    client = s3_client()
    ensure_bucket(client, args.bucket)

    if not args.users:
        print(
            "Bucket is ready. To add teammate IAM users later:\n"
            f"  python aws_setup.py --bucket {args.bucket} --users name1 name2"
        )
        return

    for name in args.users:
        try:
            creds = create_uploader_user(name, args.bucket)
            print_env_block(creds)
        except Exception as exc:
            print(f"Could not create IAM user {name!r}: {exc}")
            print(
                "Learner Lab often blocks IAM user creation. In that case, "
                "start the lab, copy the lab AWS keys into each teammate's "
                ".env along with S3_BUCKET, and do not commit .env."
            )


if __name__ == "__main__":
    main()
