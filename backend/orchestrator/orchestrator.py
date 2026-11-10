"""Run the consultation stages in sequence and retain their outputs."""

import json
from pathlib import Path
from typing import Union

from backend.src.document_screening.implementations.document_agent import (
    run_document_screening,
)
from backend.src.email_intake import run_case_intake
from backend.src.spatial_screening.implementations.irz_code_agent import (
    run_irz_code_spatial_screening,
)


def _save_run(run: dict[str, object], output_directory: Path) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    with (output_directory / "run.json").open("w", encoding="utf-8") as output:
        json.dump(run, output, ensure_ascii=False, indent=2)
        output.write("\n")


def run_consultation(
    case_data: dict[str, object],
    model_config_path: Union[str, Path],
    output_directory: Union[str, Path],
    *,
    run_id: str,
) -> dict[str, object]:
    """Run intake, document screening, and spatial screening for one case."""
    case_data = run_case_intake(case_data)
    output_directory = Path(output_directory)
    run: dict[str, object] = {
        "run_id": run_id,
        "stage_outputs": {"email_intake": case_data},
        "warnings": [],
        "errors": [],
    }
    _save_run(run, output_directory)

    missing_fields = [
        field
        for field in ("reference_id", "lpa_website")
        if not isinstance(case_data.get(field), str) or not case_data[field].strip()
    ]
    if missing_fields:
        run["errors"].append({
            "stage": "email_intake",
            "message": "Missing required field(s): " + ", ".join(missing_fields),
        })
        _save_run(run, output_directory)
        return run

    stage_outputs = run["stage_outputs"]
    errors = run["errors"]
    warnings = run["warnings"]
    document_result = None
    try:
        document_result = run_document_screening(
            reference=case_data["reference_id"],
            base_url=case_data["lpa_website"],
            model_config_path=model_config_path,
            output_directory=output_directory,
        )
        stage_outputs["document_screening"] = document_result
    except Exception as error:
        errors.append({
            "stage": "document_screening",
            "message": f"{type(error).__name__}: {error}",
        })
    _save_run(run, output_directory)

    polygon = case_data.get("polygon")
    polygon_warning_added = False
    if polygon is None and document_result is not None:
        metadata = document_result.get("metadata", {})
        candidate = metadata.get("polygon", [])
        if isinstance(candidate, list) and candidate:
            if isinstance(candidate[0], list) and candidate[0] and isinstance(
                candidate[0][0], (list, tuple)
            ):
                if len(candidate) == 1:
                    polygon = candidate[0]
                else:
                    warnings.append({
                        "stage": "spatial_screening",
                        "message": (
                            "Spatial screening was skipped because the application "
                            "has multiple polygon rings, which the current spatial "
                            "stage does not accept."
                        ),
                    })
                    polygon_warning_added = True
            else:
                polygon = candidate

    if polygon is None or polygon == []:
        if not polygon_warning_added:
            warnings.append({
                "stage": "spatial_screening",
                "message": (
                    "Spatial screening was skipped because no usable BNG polygon was "
                    "found. Coordinate and address conversion is not implemented yet."
                ),
            })
    elif not any(
        warning["stage"] == "spatial_screening"
        for warning in warnings
    ):
        context: dict[str, object] = {"case_data": case_data}
        if document_result is not None:
            context["document_screening"] = document_result.get("final_output", {})
        try:
            stage_outputs["spatial_screening"] = run_irz_code_spatial_screening(
                polygon=polygon,
                context=context,
                model_config_path=model_config_path,
            )
        except Exception as error:
            errors.append({
                "stage": "spatial_screening",
                "message": f"{type(error).__name__}: {error}",
            })

    _save_run(run, output_directory)
    return run