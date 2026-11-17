"""Run the consultation stages in sequence and retain their outputs."""

import json
from datetime import date
from pathlib import Path
from typing import Union
from uuid import uuid4

import yaml

from backend.src.document_screening.implementations.document_agent import (
    run_document_screening,
)
from backend.src.case_intake.implementations.placeholder import run_case_intake
from backend.src.decision.implementations.decision_module import run_decision
from backend.src.spatial_screening.implementations.irz_code_agent import (
    run_irz_code_spatial_screening,
)

_DEFAULT_ORCHESTRATOR_CONFIG = (
    Path(__file__).resolve().parents[2]
    / "environment"
    / "configs"
    / "orchestrator.yaml"
)


def _load_required_fields(config_path: Path) -> dict[str, tuple[str, ...]]:
    try:
        with config_path.open(encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid orchestrator YAML in {config_path}: {error}") from error

    if not isinstance(config, dict):
        raise ValueError(f"Orchestrator config {config_path} must be a YAML mapping.")
    required_fields = config.get("required_fields")
    if not isinstance(required_fields, dict):
        raise ValueError("'required_fields' config section must be a mapping.")

    stages = ("case_intake", "document_screening", "spatial_screening", "decision")
    if required_fields.keys() != set(stages):
        raise ValueError(
            "'required_fields' must define case_intake, document_screening, "
            "spatial_screening, and decision."
        )

    result = {}
    for stage in stages:
        fields = required_fields[stage]
        if not isinstance(fields, list) or any(
            not isinstance(field, str) or not field.strip() for field in fields
        ):
            raise ValueError(f"Required fields for {stage} must be a list of non-empty strings.")
        result[stage] = tuple(fields)
    return result


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


def _check_required_fields(output: dict, fields: tuple[str, ...]) -> None:
    if not isinstance(output, dict):
        raise TypeError("Stage output must be a JSON object.")
    json.dumps(output)

    def get_nested_value(field_path: str):
        value = output
        for field in field_path.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(field)
        return value

    missing_fields = [
        field for field in fields
        if (value := get_nested_value(field)) is None
        or (isinstance(value, str) and not value.strip())
        or (isinstance(value, (list, dict)) and not value)
    ]
    if missing_fields:
        raise ValueError("Missing required field(s): " + ", ".join(missing_fields))


def _extract_intake_value(case_intake: dict, field_name: str) -> str:
    field = case_intake.get(field_name)
    if not isinstance(field, dict):
        raise TypeError(f"{field_name} must be a Field object.")
    value = field.get("value")
    if field.get("status") != "stated" or not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must have a stated, non-empty value.")
    return value.strip()


def _extract_consultation_date(case_intake: dict) -> date | None:
    field = case_intake.get("consultation_date")
    if field is None:
        return None
    if not isinstance(field, dict):
        raise TypeError("consultation_date must be a Field object when provided.")
    if field.get("status") != "stated":
        return None
    value = field.get("value")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A stated consultation_date must have a non-empty value.")
    try:
        return date.fromisoformat(value.strip())
    except ValueError as error:
        raise ValueError("consultation_date must use YYYY-MM-DD format.") from error


def _build_decision_input(
    case_intake: dict,
    document_evidence: dict,
    spatial_screening: dict,
) -> dict[str, dict]:
    case_intake_signals = case_intake["signals"]
    document_signals = document_evidence["consultation_type_signals"]
    spatial_checks = spatial_screening["geospatial_checks"]
    return {
        "case_intake": {
            field: case_intake_signals[field]
            for field in (
                "eia_screening",
                "eia_scoping",
                "conditions",
                "pre_application",
                "prior_approval",
                "minerals_waste_existing",
                "minerals_waste_new",
            )
        },
        "document_screening": {
            field: document_signals[field]
            for field in (
                "amendment",
                "minerals_waste_existing",
                "minerals_waste_new",
            )
        },
        "spatial_screening": {
            "best_and_most_versatile_land": spatial_checks[
                "best_and_most_versatile_land"
            ],
            "answers": spatial_screening["answers"],
        },
    }


def run_consultation(
    case_data: dict[str, object],
    model_config_path: Union[str, Path],
    output_directory: Union[str, Path],
    *,
    run_id: str | None = None,
) -> dict[str, object]:
    """Run intake, document screening, and spatial screening for one case."""

    run: dict = {
        "run_id": run_id if run_id is not None else uuid4().hex,
        "stage_outputs": {"case_intake": {},
                          "document_screening": {},
                          "spatial_screening": {},
                          "decision": {}},
        "errors": [],
    }

    output_directory = Path(output_directory)
    try:
        required_fields = _load_required_fields(_DEFAULT_ORCHESTRATOR_CONFIG)
    except Exception as error:
        run["errors"].append({
            "stage": "configuration",
            "message": f"{type(error).__name__}: {error}",
        })
        _save_run(run, output_directory)
        return run

    # Run case intake stage
    try:
        case_intake = run_case_intake(case_data)
        _check_required_fields(case_intake, ())
        run["stage_outputs"]["case_intake"] = case_intake
        _check_required_fields(case_intake, required_fields["case_intake"])
        reference = _extract_intake_value(case_intake, "lpa_reference_id")
        base_url = _extract_intake_value(case_intake, "lpa_website")
        consultation_date = _extract_consultation_date(case_intake)
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
        _check_required_fields(document_result, ())
        run["stage_outputs"]["document_screening"] = document_result
        document_evidence = document_result.get("final_output")
        if not isinstance(document_evidence, dict):
            raise ValueError("Document screening result must contain a final_output object.")
        _check_required_fields(
            document_evidence, required_fields["document_screening"]
        )
        polygon_output = document_evidence["polygon"]
        if not isinstance(polygon_output, dict):
            raise ValueError("Document screening final_output polygon must be an object.")
        polygon_rings = polygon_output.get("polygon")
        if not isinstance(polygon_rings, list) or len(polygon_rings) != 1:
            raise ValueError("Document screening must provide exactly one BNG polygon ring.")
        polygon = polygon_rings[0]
        if not isinstance(polygon, list) or not polygon:
            raise ValueError("Document screening must provide a non-empty BNG polygon ring.")
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
        _check_required_fields(spatial_screening, ())
        run["stage_outputs"]["spatial_screening"] = spatial_screening
        _check_required_fields(spatial_screening, required_fields["spatial_screening"])
    except Exception as error:
        run["errors"].append({
            "stage": "spatial_screening",
            "message": f"{type(error).__name__}: {error}",
        })
        _save_run(run, output_directory)
        return run

    # Run decision stage
    try:
        decision_input = _build_decision_input(
            case_intake, document_evidence, spatial_screening
        )
        decision_result = run_decision(decision_input)
        _check_required_fields(decision_result, required_fields["decision"])
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