from pathlib import Path
from backend.src.case_intake.tools.email_parser import parse_email
from backend.src.case_intake.tools.email_extractor import extract_from_consultation
from backend.src.utilities.s3_utils import create_s3_client


def execute_case_intake_stage(
    *,
    email_file_path: str | Path,
    bucket: str,
    folder_prefix: str,
    consultation_id: str,
    model_config_path: str | Path,
    prompt_path: str | Path,
    overwrite: bool = False,
) -> dict:
    if not email_file_path:
        raise ValueError("An email file is required.")

    s3_client = create_s3_client()

    parsed_email = parse_email(
        email_file_path=email_file_path,
        s3_client=s3_client,
        bucket=bucket,
        folder_prefix=folder_prefix,
        consultation_id=consultation_id,
    )

    payload = parsed_email["json"]
    metadata = payload.get("metadata", {})
    if metadata.get("parse_status") != "ok" or not parsed_email["s3_key"]:
        raise ValueError(f"Email processing incomplete: {metadata}")

    stored_prefix, stored_consultation_id = (
        payload["storage"]["prefix"].rsplit("/", 2)[:2]
    )

    extraction = extract_from_consultation(
        s3_client=s3_client,
        bucket=bucket,
        folder_prefix=stored_prefix,
        consultation_id=stored_consultation_id,
        model_config_path=model_config_path,
        prompt_path=prompt_path,
        overwrite=overwrite,
    )

    records = extraction["json"]["records"]

    if not records:
        raise ValueError("No email extraction records were found.")

    warnings = []
    if len(records) > 1:
        warnings.append(
            f"Found {len(records)} email records; returning the first only. There should only be one in production."
        )

    return {
        "email_extraction": records[0]["extractions"],
        #"warnings": warnings,
    }