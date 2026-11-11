"""Run the consultation stages in sequence and retain their outputs."""

import json
from pathlib import Path
from typing import Union
from uuid import uuid4

import yaml

from backend.src.document_screening.implementations.document_agent import (
    run_document_screening,
)
from backend.src.case_intake.implementations.placeholder import run_case_intake
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

    stages = ("case_intake", "document_screening", "spatial_screening")
    if required_fields.keys() != set(stages):
        raise ValueError("'required_fields' must define case_intake, document_screening, and spatial_screening.")

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
    missing_fields = [
        field for field in fields
        if output.get(field) is None
        or (isinstance(output[field], str) and not output[field].strip())
        or (isinstance(output[field], (list, dict)) and not output[field])
    ]
    if missing_fields:
        raise ValueError("Missing required field(s): " + ", ".join(missing_fields))


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
                          "spatial_screening": {}},
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
        for field in required_fields["case_intake"]:
            if not isinstance(case_intake[field], str):
                raise TypeError(f"{field} must be a non-empty string.")
    except Exception as error:
        run["errors"].append({
            "stage": "case_intake",
            "message": f"{type(error).__name__}: {error}",
        })
        _save_run(run, output_directory)
        return run

    # Run document screening stage
    try:
        document_result = run_document_screening(
            reference=case_intake["reference_id"],
            base_url=case_intake["lpa_website"],
            model_config_path=model_config_path,
            output_directory=output_directory,
        )
        _check_required_fields(document_result, ())
        run["stage_outputs"]["document_screening"] = document_result
        document_evidence = document_result.get("final_output")
        if document_evidence is None:
            document_evidence = document_result.get("metadata", {})
        _check_required_fields(
            document_evidence, required_fields["document_screening"]
        )
        polygon_rings = document_evidence["polygon"]
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
        if not isinstance(spatial_screening["polygon"], list):
            raise TypeError("Spatial screening polygon must be a coordinate list.")
    except Exception as error:
        run["errors"].append({
            "stage": "spatial_screening",
            "message": f"{type(error).__name__}: {error}",
        })
        _save_run(run, output_directory)
        return run

    _save_run(run, output_directory)
    return run