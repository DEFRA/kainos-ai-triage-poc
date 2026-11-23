import csv
import os
import sys
from datetime import datetime
from pathlib import Path

project_root = next(
    path for path in (Path.cwd(), *Path.cwd().parents)
    if (path / "pyproject.toml").is_file()
)
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from backend.orchestrator.implementations.s3_orchestrator import run_s3_consultation
from backend.src.utilities.s3_utils import create_s3_client

# Case intake creates its own S3 client, so the profile is also set in the environment.
os.environ["AWS_PROFILE"] = "yazan-iam-dev"
s3_client = create_s3_client(region="eu-west-2", profile_name="yazan-iam-dev")

bucket = "ai-triage-test"
# A fresh prefix per run: the spatial and decision results are overwritten at the same key.
run_stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
test_prefix = f"s3-end-to-end/{run_stamp}"
model_config_path = project_root / "environment" / "model_configs" / "bedrock-aws-nova-pro.yaml"
intake_prompt_path = project_root / "backend" / "src" / "case_intake" / "prompts" / "case_intake_extraction.md"

emails_root = project_root / "data" / "emails"  # set to the folder containing necessary/ and unnecessary/
results_path = project_root / "data" / f"end_to_end_results_{run_stamp}.csv"
rows = []

for ground_truth in ("necessary", "unnecessary"):
    for consultation_folder in sorted((emails_root / ground_truth).iterdir()):
        if not consultation_folder.is_dir():
            continue
        consultation_id = consultation_folder.name
        for email_file_path in sorted(consultation_folder.glob("*.eml")):
            row = {
                "consultation_id": consultation_id,
                "ground_truth": ground_truth,
                "predicted": None,
                "consultation_date": None,
                "lpa_authority": None,
                "error_message": None,
                "run_id": None,
                "email_file": email_file_path.name,
            }
            try:
                run = run_s3_consultation(
                    email_file_path=email_file_path,
                    consultation_id=consultation_id,
                    intake_prompt_path=intake_prompt_path,
                    model_config_path=model_config_path,
                    bucket=bucket,
                    prefix=f"{test_prefix}/{ground_truth}/{consultation_id}/{email_file_path.stem}",
                    s3_client=s3_client,
                )
                row["run_id"] = run["run_id"]
                row["predicted"] = run["stage_outputs"]["decision"].get("classification")
                row["consultation_date"] = run["stage_outputs"]["case_intake"]["consultation_date"].get("value")
                row["lpa_authority"] = run["stage_outputs"]["case_intake"]["lpa_name"].get("value")
                if run["errors"]:
                    row["error_message"] = "; ".join(f"{e['stage']}: {e['message']}" for e in run["errors"])
                elif row["predicted"] is None:
                    row["error_message"] = "No final decision produced."
            except Exception as error:
                row["error_message"] = f"{type(error).__name__}: {error}"
            rows.append(row)

with results_path.open("w", newline="", encoding="utf-8") as file:
    writer = csv.DictWriter(
        file,
        fieldnames=["consultation_id", "ground_truth", "predicted", "consultation_date", "lpa_authority", "error_message", "run_id", "email_file"],
    )
    writer.writeheader()
    writer.writerows(rows)
print(results_path)