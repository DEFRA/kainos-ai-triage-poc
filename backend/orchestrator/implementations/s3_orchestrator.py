"""Run the consultation stages in sequence and store their outputs under S3."""

from pathlib import Path
from datetime import datetime
import traceback
from typing import Any, Optional, Union
from uuid import uuid4

from backend.src.case_intake.implementations.case_intake_orchestrator import (
    execute_case_intake_stage,
)
from backend.src.decision.implementations.s3_decision_agent import run_s3_decision
from backend.src.document_screening.implementations.s3_document_agent import (
    run_s3_document_screening,
)
from backend.src.spatial_screening.implementations.s3_spatial_agent import (
    run_s3_spatial_screening,
)
from backend.src.utilities.s3_utils import create_s3_client, upload_json_to_s3
from ..tools.utilities import (
    STAGES,
    build_decision_input,
    check_required_fields,
    extract_consultation_date,
    extract_intake_value,
    extract_polygon,
    load_model_configs,
    load_required_fields,
)

_DEFAULT_ORCHESTRATOR_CONFIG = (
    Path(__file__).resolve().parents[3]
    / "environment"
    / "configs"
    / "orchestrator.yaml"
)
_RUN_NAME = "run.json"


def _save_run(run: dict, s3_client: Any, bucket: str, prefix: str) -> bool:
    # A failed save is recorded on the run so the consultation result is still returned.
    try:
        upload_json_to_s3(s3_client, bucket, prefix, run, json_name=_RUN_NAME)
    except Exception as error:
        run["errors"].append({
            "stage": "persistence",
            "message": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc(),
        })
        return False
    return True


def run_s3_consultation(
    *,
    email_file_path: Union[str, Path],
    consultation_id: str,
    intake_prompt_path: Union[str, Path],
    model_config_path: Optional[Union[str, Path]] = None,
    bucket: str,
    prefix: str,
    implementation_config_path: Optional[Union[str, Path]] = None,
    s3_client: Optional[Any] = None,
) -> dict:
    """Run intake, document screening, spatial screening, and decision, storing each stage under S3."""
    if not isinstance(bucket, str) or not bucket.strip():
        raise ValueError("bucket must be a non-empty string.")
    if not isinstance(prefix, str) or not prefix.strip().strip("/"):
        raise ValueError("prefix must be a non-empty S3 run prefix.")
    bucket = bucket.strip()
    prefix = prefix.strip().strip("/")

    if s3_client is None:
        s3_client = create_s3_client()

    run: dict = {
        "run_id": uuid4().hex,
        "consultation_id": consultation_id,
        "stage_outputs": {stage: {} for stage in STAGES},
        "errors": [],
    }

    config_path = (
        Path(implementation_config_path)
        if implementation_config_path is not None
        else _DEFAULT_ORCHESTRATOR_CONFIG
    )
    try:
        required_fields = load_required_fields(config_path)
        model_configs = load_model_configs(config_path)
    except Exception as error:
        run["errors"].append({
            "stage": "configuration",
            "message": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc(),
        })
        _save_run(run, s3_client, bucket, prefix)
        return run

    # Run case intake stage
    try:
        intake_result = execute_case_intake_stage(
            email_file_path=email_file_path,
            bucket=bucket,
            folder_prefix=f"{prefix}/case_intake",
            consultation_id=consultation_id,
            model_config_path=model_configs.get("case_intake", model_config_path),
            prompt_path=intake_prompt_path,
        )
        case_intake = intake_result["email_extraction"]
        check_required_fields(case_intake, ())
        run["stage_outputs"]["case_intake"] = case_intake
        check_required_fields(case_intake, required_fields["case_intake"])
        reference = extract_intake_value(case_intake, "lpa_reference_id")
        base_url = extract_intake_value(case_intake, "lpa_website")
        consultation_date = extract_consultation_date(case_intake)
    except Exception as error:
        run["errors"].append({
            "stage": "case_intake",
            "message": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc(),
        })
        _save_run(run, s3_client, bucket, prefix)
        return run

    # Run document screening stage
    try:
        document_screening_arguments = {
            "bucket": bucket,
            "prefix": f"{prefix}/document_screening",
            "s3_client": s3_client,
        }
        if consultation_date is not None: 
            document_screening_arguments["ignore_after"] = consultation_date
            
        document_result = run_s3_document_screening(
            reference,
            base_url,
            model_configs.get("document_screening", model_config_path),
            **document_screening_arguments,
        )
        check_required_fields(document_result, ())
        run["stage_outputs"]["document_screening"] = document_result
        document_evidence = document_result.get("final_output")
        if not isinstance(document_evidence, dict):
            raise ValueError("Document screening result must contain a final_output object.")
        check_required_fields(document_evidence, required_fields["document_screening"])
        polygon = extract_polygon(document_evidence)
    except Exception as error:
        run["errors"].append({
            "stage": "document_screening",
            "message": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc(),
        })
        _save_run(run, s3_client, bucket, prefix)
        return run

    # Run spatial screening stage
    try:
        spatial_screening = run_s3_spatial_screening(
            polygon,
            {"case_data": case_intake, "document_screening": document_evidence},
            model_configs.get("spatial_screening", model_config_path),
            bucket=bucket,
            prefix=f"{prefix}/spatial_screening",
            s3_client=s3_client,
        )
        check_required_fields(spatial_screening, ())
        run["stage_outputs"]["spatial_screening"] = spatial_screening
        check_required_fields(spatial_screening, required_fields["spatial_screening"])
    except Exception as error:
        run["errors"].append({
            "stage": "spatial_screening",
            "message": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc(),
        })
        _save_run(run, s3_client, bucket, prefix)
        return run

    # Run decision stage
    try:
        decision_input = build_decision_input(
            case_intake, document_evidence, spatial_screening
        )
        decision_result = run_s3_decision(
            decision_input,
            bucket=bucket,
            prefix=f"{prefix}/decision",
            s3_client=s3_client,
        )
        check_required_fields(decision_result, required_fields["decision"])
        run["stage_outputs"]["decision"] = decision_result
    except Exception as error:
        run["errors"].append({
            "stage": "decision",
            "message": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc(),
        })
        _save_run(run, s3_client, bucket, prefix)
        return run

    _save_run(run, s3_client, bucket, prefix)
    return run
