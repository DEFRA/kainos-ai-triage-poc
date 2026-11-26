import json
from typing import Any, Optional
import boto3
from botocore.config import Config


def create_s3_client(
    *,
    region: str = "eu-west-2",
    profile_name: Optional[str] = None,
) -> Any:
    """Create an S3 client using the default credentials or a specified profile."""

    # Uses boto3's credential chain by default; profile_name selects a specific profile.
    # Set AWS_PROFILE environment variable to AWS profile
    session = boto3.Session(profile_name=profile_name, region_name=region)
    return session.client("s3")


def print_s3_directory_structure(
    s3_client: Any,
    bucket: str,
    prefix: str = "",
    indent: int = 0,
) -> None:
    """Print the S3 objects and subfolders under a prefix as an indented tree."""

    paginator = s3_client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/")

    for page in pages:
        for folder in page.get("CommonPrefixes", []):
            child_prefix = folder["Prefix"]
            name = child_prefix[len(prefix):].rstrip("/")
            print("  " * indent + f"{name}/")
            print_s3_directory_structure(s3_client, bucket, child_prefix, indent + 1)

        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key != prefix:
                name = key[len(prefix):]
                if "/" not in name:
                    print("  " * indent + name)


def put_object_with_metadata(
    s3_client: Any,
    bucket: str,
    key: str,
    body: bytes,
) -> dict[str, Any]:
    """Upload bytes to S3 and return the object location and version metadata."""

    response = s3_client.put_object(Bucket=bucket, Key=key, Body=body)
    return {
        "bucket": bucket,
        "key": key,
        "version": response.get("VersionId"),
        "s3_uri": f"s3://{bucket}/{key}",
    }


def upload_json_to_s3(
    s3_client: Any,
    bucket: str,
    prefix: str,
    payload: dict[str, Any],
    json_name: str = "email_metadata.json",
) -> dict[str, Any]:
    """Serialize and upload a JSON payload, returning its S3 location and version."""

    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    return put_object_with_metadata(
        s3_client,
        bucket,
        f"{prefix}/{json_name}",
        body,
    )