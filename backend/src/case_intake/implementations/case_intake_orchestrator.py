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
    overwrite: bool = True,
) -> dict:
    if not email_file_path:
        raise ValueError("An email file is required.")

    s3_client = create_s3_client()
    warnings = []

    parsed_email = parse_email(
        email_file_path=email_file_path,
        s3_client=s3_client,
        bucket=bucket,
        folder_prefix=folder_prefix,
        consultation_id=consultation_id,
        overwrite=overwrite,
    )

    warnings.extend(parsed_email["warnings"])
    if parsed_email["status"] == "skipped":
        return {"status": "skipped", "warnings": warnings}

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

    warnings.extend(extraction["warnings"])
    if extraction["status"] == "skipped":
        return {"status": "skipped", "warnings": warnings}

    records = extraction["json"]["records"]

    if not records:
        raise ValueError("No email extraction records were found.")

    if len(records) > 1:
        warnings.append(
            f"Found {len(records)} email records; returning the first only. There should only be one in production."
        )
    record = records[0]["extractions"]

    return {
        "status": "completed",
        "warnings": warnings,
        # Should be 1 email per consultation but this handles multiple records
        # Consultations with more than 1 email should have been manually cleaned from raw folders where possible
        # This allows the code to handle multiple records, even though only one is expected in production
        # 'QC' filter works for most but not all
        "email_extraction": record,
    }