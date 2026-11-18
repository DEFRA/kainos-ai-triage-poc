"""Run the consultation stages in sequence and retain their outputs locally."""

import json
from pathlib import Path
from typing import Union
from uuid import uuid4

from backend.src.document_screening.implementations.document_agent import (
    run_document_screening,
)
from backend.src.decision.implementations.decision_module import run_decision
from backend.src.spatial_screening.implementations.irz_code_agent import (
    run_irz_code_spatial_screening,
)
from ..tools.utilities import (
    STAGES,
    build_decision_input,
    check_required_fields,
    extract_consultation_date,
    extract_intake_value,
    extract_polygon,
    load_required_fields,
)

_DEFAULT_ORCHESTRATOR_CONFIG = (
    Path(__file__).resolve().parents[3]
    / "environment"
    / "configs"
    / "orchestrator.yaml"
)


def _save_run(run: dict, output_directory: Path) -> bool:
    try:
        content = json.dumps(run, ensure_ascii=False, indent=2)
        output_directory.mkdir(parents=True, exist_ok=True)
        temporary_path = output_directory / "run.json.tmp"
        temporary_path.write_text(content + "\n", encoding="utf-8")
        temporary_path.replace(output_directory / "run.json")
    except (OSError, TypeError, ValueError) as error:
        run["errors"].append({
            "stage": "persistence",
            "message": f"{type(error).__name__}: {error}",
        })
        return False
    return True


def run_consultation(
    case_data: dict[str, object],
    model_config_path: Union[str, Path],
    output_directory: Union[str, Path],
    *,
    run_id: str | None = None,
) -> dict[str, object]:
    """Run document, spatial, and decision stages for one case.

    case_data is the case intake output. The intake stage is not run here because it requires an
    email file and S3 storage.
    """

    run: dict = {
        "run_id": run_id if run_id is not None else uuid4().hex,
        "stage_outputs": {stage: {} for stage in STAGES},
        "errors": [],
    }

    output_directory = Path(output_directory)
    try:
        required_fields = load_required_fields(_DEFAULT_ORCHESTRATOR_CONFIG)
    except Exception as error:
        run["errors"].append({
            "stage": "configuration",
            "message": f"{type(error).__name__}: {error}",
        })
        _save_run(run, output_directory)
        return run

    # Validate the supplied case intake output
    try:
        case_intake = case_data
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
        })
        _save_run(run, output_directory)
        return run

    # Run document screening stage
    try:
        document_screening_arguments = {
            "reference": reference,
            "base_url": base_url,
            "model_config_path": model_config_path,
            "output_directory": output_directory,
        }
        if consultation_date is not None:
            document_screening_arguments["ignore_after"] = consultation_date
        document_result = run_document_screening(**document_screening_arguments)
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
        })
        _save_run(run, output_directory)
        return run

    # Run spatial screening stage
    try:
        spatial_screening = run_irz_code_spatial_screening(
            polygon=polygon,
            context={"case_data": case_intake, "document_screening": document_evidence},
            model_config_path=model_config_path,
        )
        check_required_fields(spatial_screening, ())
        run["stage_outputs"]["spatial_screening"] = spatial_screening
        check_required_fields(spatial_screening, required_fields["spatial_screening"])
    except Exception as error:
        run["errors"].append({
            "stage": "spatial_screening",
            "message": f"{type(error).__name__}: {error}",
        })
        _save_run(run, output_directory)
        return run

    # Run decision stage
    try:
        decision_input = build_decision_input(
            case_intake, document_evidence, spatial_screening
        )
        decision_result = run_decision(decision_input)
        check_required_fields(decision_result, required_fields["decision"])
        run["stage_outputs"]["decision"] = decision_result
    except Exception as error:
        run["errors"].append({
            "stage": "decision",
            "message": f"{type(error).__name__}: {error}",
        })
        _save_run(run, output_directory)
        return run

    _save_run(run, output_directory)
    return run
