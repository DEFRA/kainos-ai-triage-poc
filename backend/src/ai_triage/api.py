"""Synchronous HTTP interface for consultation runs."""

import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException

from orchestrator.orchestrator import run_consultation


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_OUTPUT_ROOT = _PROJECT_ROOT / "data" / "runs"


def create_app(
    *,
    model_config_path: str | Path | None = None,
    output_root: str | Path = _DEFAULT_OUTPUT_ROOT,
) -> FastAPI:
    configured_model_path = model_config_path or os.environ.get("AI_TRIAGE_MODEL_CONFIG")
    if configured_model_path is not None:
        configured_model_path = Path(configured_model_path)
        if not configured_model_path.is_absolute():
            configured_model_path = _PROJECT_ROOT / configured_model_path
    run_root = Path(output_root)
    if not run_root.is_absolute():
        run_root = _PROJECT_ROOT / run_root

    app = FastAPI(title="AI Triage")

    @app.post("/consultations")
    def create_consultation(case_data: dict[str, object]) -> dict[str, object]:
        if configured_model_path is None or not configured_model_path.is_file():
            raise HTTPException(
                status_code=503,
                detail="Set AI_TRIAGE_MODEL_CONFIG to a valid model configuration file.",
            )
        run_id = uuid4().hex
        return run_consultation(
            case_data,
            model_config_path=configured_model_path,
            output_directory=run_root / run_id,
            run_id=run_id,
        )

    @app.get("/consultations/{run_id}")
    def get_consultation(run_id: str) -> dict[str, object]:
        try:
            normalized_run_id = UUID(run_id).hex
        except ValueError as error:
            raise HTTPException(status_code=404, detail="Consultation run not found.") from error
        result_path = run_root / normalized_run_id / "run.json"
        if not result_path.is_file():
            raise HTTPException(status_code=404, detail="Consultation run not found.")
        return json.loads(result_path.read_text(encoding="utf-8"))

    return app


app = create_app()