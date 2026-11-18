from typing import Any, Optional

from backend.src.utilities.s3_utils import create_s3_client, upload_json_to_s3
from ..tools.decision import run_decision


_RESULT_NAME = "decision.json"


def run_s3_decision(
    stage_outputs: dict,
    *,
    bucket: str,
    prefix: str,
    s3_client: Optional[Any] = None,
) -> dict:
    """Make the consultation decision and store the complete result under an S3 run prefix."""
    if not isinstance(bucket, str) or not bucket.strip():
        raise ValueError("bucket must be a non-empty string.")
    if not isinstance(prefix, str) or not prefix.strip().strip("/"):
        raise ValueError("prefix must be a non-empty S3 run prefix.")
    bucket = bucket.strip()
    prefix = prefix.strip().strip("/")

    if s3_client is None:
        s3_client = create_s3_client()

    result = run_decision(stage_outputs)

    # The stored file is the result without its storage location, which is added after upload.
    try:
        storage = upload_json_to_s3(
            s3_client, bucket, prefix, result, json_name=_RESULT_NAME
        )
    except Exception as error:
        raise RuntimeError(
            f"Could not upload decision result to s3://{bucket}/{prefix}/{_RESULT_NAME}."
        ) from error
    result["storage"] = storage
    return result
