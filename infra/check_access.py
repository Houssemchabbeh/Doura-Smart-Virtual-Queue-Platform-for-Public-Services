"""Smoke test: verify AWS credentials, region and LabRole before provisioning anything.

Read-only: creates nothing, costs nothing.
Usage:  python -m infra.check_access
"""
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from infra.config import LAB_ROLE_NAME, REGION


def main() -> int:
    session = boto3.Session(region_name=REGION)
    try:
        identity = session.client("sts").get_caller_identity()
        print(f"[ok] account  : {identity['Account']}")
        print(f"[ok] caller   : {identity['Arn']}")
        print(f"[ok] region   : {session.region_name}")

        role = session.client("iam").get_role(RoleName=LAB_ROLE_NAME)["Role"]
        print(f"[ok] LabRole  : {role['Arn']}")

        tables = session.client("dynamodb").list_tables()["TableNames"]
        print(f"[ok] dynamodb : {len(tables)} table(s)")

        buckets = session.client("s3").list_buckets()["Buckets"]
        print(f"[ok] s3       : {len(buckets)} bucket(s)")
    except (ClientError, BotoCoreError) as exc:
        print(f"[fail] {exc}", file=sys.stderr)
        print("Hint: copy the Learner Lab 'AWS Details' credentials into ~/.aws/credentials "
              "(session token expires every ~4h).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
