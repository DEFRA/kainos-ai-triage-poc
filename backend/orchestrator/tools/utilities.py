"""Shared helpers for the consultation orchestrator implementations."""

import json
from datetime import date, datetime
from pathlib import Path

from attrs import field
import yaml


STAGES = ("case_intake", "document_screening", "spatial_screening", "decision")


def load_model_configs(config_path: Path) -> dict[str, Path]:
    with config_path.open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)
    model_configs = config.get("model_configs", {})
    if not isinstance(model_configs, dict):
        raise ValueError("'model_configs' config section must be a mapping.")
    supported_stages = {"case_intake", "document_screening", "spatial_screening"}
    if model_configs.keys() - supported_stages:
        raise ValueError("'model_configs' contains an unsupported stage.")
    result = {}
    for stage, value in model_configs.items():
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Model config for {stage} must be a non-empty path or null.")
        path = Path(value.strip()).expanduser()
        result[stage] = path if path.is_absolute() else Path(__file__).resolve().parents[3] / path
    return result


def load_required_fields(config_path: Path) -> dict[str, tuple[str, ...]]:
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

    if required_fields.keys() != set(STAGES):
        raise ValueError(
            "'required_fields' must define case_intake, document_screening, "
            "spatial_screening, and decision."
        )

    result = {}
    for stage in STAGES:
        fields = required_fields[stage]
        if not isinstance(fields, list) or any(
            not isinstance(field, str) or not field.strip() for field in fields
        ):
            raise ValueError(f"Required fields for {stage} must be a list of non-empty strings.")
        result[stage] = tuple(fields)
    return result


def check_required_fields(output: dict, fields: tuple[str, ...]) -> None:
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


def extract_intake_value(case_intake: dict, field_name: str) -> str:
    field = case_intake.get(field_name)
    if not isinstance(field, dict):
        raise TypeError(f"{field_name} must be a Field object.")
    value = field.get("value")
    if field.get("status") != "stated" or not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must have a stated, non-empty value.")
    return value.strip()


def extract_consultation_date(case_intake: dict) -> date | None:
    field = case_intake.get("consultation_date")
    if field is None:
        return None
    if not isinstance(field, dict):
        raise TypeError("consultation_date must be a Field object when provided.")
    if field.get("status") != "stated":
        return None
    value = field.get("value")

    try:
        value = datetime.strptime(
            field.get("value"),
            "%Y/%m/%d"
        ).date()
    except ValueError:
        print("Invalid consultation date format for consultation: ", value)
        value = None
        
    return value


def build_decision_input(
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


def extract_polygon(document_evidence: dict) -> list:
    polygon_output = document_evidence["polygon"]
    if not isinstance(polygon_output, dict):
        raise ValueError("Document screening final_output polygon must be an object.")
    polygon_rings = polygon_output.get("polygon")
    if not isinstance(polygon_rings, list) or len(polygon_rings) != 1:
        raise ValueError("Document screening must provide exactly one BNG polygon ring.")
    polygon = polygon_rings[0]
    if not isinstance(polygon, list) or not polygon:
        raise ValueError("Document screening must provide a non-empty BNG polygon ring.")
    return polygon
