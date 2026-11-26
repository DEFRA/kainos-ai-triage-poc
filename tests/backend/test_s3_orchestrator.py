import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from backend.orchestrator.implementations.s3_orchestrator import run_s3_consultation


ORCHESTRATOR_MODULE = "backend.orchestrator.implementations.s3_orchestrator"
CONFIG = (
    "required_fields:\n"
    "  case_intake: []\n"
    "  document_screening: []\n"
    "  spatial_screening: []\n"
    "  decision: []\n"
)
SIGNAL_FIELDS = (
    "eia_screening", "eia_scoping", "conditions", "pre_application",
    "prior_approval", "minerals_waste_existing", "minerals_waste_new",
)


def case_intake():
    return {
        "lpa_reference_id": {"status": "stated", "value": "REF-1"},
        "lpa_website": {"status": "stated", "value": "https://lpa.example"},
        "consultation_date": {"status": "stated", "value": "2025-01-02"},
        "signals": {field: "no" for field in SIGNAL_FIELDS},
    }


def document_result():
    return {
        "final_output": {
            "polygon": {"polygon": [[[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]]},
            "consultation_type_signals": {
                "amendment": "no",
                "minerals_waste_existing": "no",
                "minerals_waste_new": "no",
            },
        },
    }


def spatial_result():
    return {
        "geospatial_checks": {"best_and_most_versatile_land": {"intersects": False}},
        "answers": [],
    }


class S3OrchestratorTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config_path = Path(directory.name) / "orchestrator.yaml"
        self.config_path.write_text(CONFIG, encoding="utf-8")
        self.s3 = Mock()

        patches = {
            "intake": patch(f"{ORCHESTRATOR_MODULE}.execute_case_intake_stage",
                            return_value={"email_extraction": case_intake()}),
            "document": patch(f"{ORCHESTRATOR_MODULE}.run_s3_document_screening",
                              return_value=document_result()),
            "spatial": patch(f"{ORCHESTRATOR_MODULE}.run_s3_spatial_screening",
                             return_value=spatial_result()),
            "decision": patch(f"{ORCHESTRATOR_MODULE}.run_s3_decision",
                              return_value={"outcome": "no_objection"}),
            "save": patch(f"{ORCHESTRATOR_MODULE}.upload_json_to_s3",
                          return_value={"key": "unused"}),
        }
        self.mocks = {name: self.enterContext(item) for name, item in patches.items()}

    def run_consultation(self, **overrides):
        arguments = {
            "email_file_path": "email.eml",
            "consultation_id": "C-1",
            "intake_prompt_path": "intake.md",
            "model_config_path": "model.yaml",
            "bucket": "bucket",
            "prefix": "/run-1/",
            "implementation_config_path": self.config_path,
            "s3_client": self.s3,
        }
        arguments.update(overrides)
        return run_s3_consultation(**arguments)

    def test_successful_run_stores_each_stage_under_its_prefix(self):
        run = self.run_consultation()

        self.assertEqual(run["errors"], [])
        self.assertEqual(self.mocks["intake"].call_args.kwargs["model_config_path"], "model.yaml")
        self.assertEqual(self.mocks["document"].call_args.args[2], "model.yaml")
        self.assertEqual(self.mocks["spatial"].call_args.args[2], "model.yaml")
        self.assertEqual(run["stage_outputs"]["decision"], {"outcome": "no_objection"})
        self.assertEqual(
            self.mocks["intake"].call_args.kwargs["folder_prefix"], "run-1/case_intake"
        )
        self.assertEqual(
            self.mocks["document"].call_args.kwargs["prefix"], "run-1/document_screening"
        )
        self.assertEqual(
            self.mocks["spatial"].call_args.kwargs["prefix"], "run-1/spatial_screening"
        )
        self.assertEqual(self.mocks["decision"].call_args.kwargs["prefix"], "run-1/decision")
        self.assertEqual(self.mocks["document"].call_args.kwargs["ignore_after"].isoformat(),
                         "2025-01-02")
        self.mocks["save"].assert_called_with(self.s3, "bucket", "run-1", run, json_name="run.json")

    def test_incomplete_development_is_forwarded_to_spatial_screening(self):
        completeness = {
            "complete": False,
            "missing_fields": ["scale_and_type_of_development.residential"],
            "incomplete_development_fields": ["scale_and_type_of_development.residential"],
        }
        self.mocks["document"].return_value["final_output"]["screening_completeness"] = completeness
        self.mocks["spatial"].return_value["human_review_required"] = True

        run = self.run_consultation()

        context = self.mocks["spatial"].call_args.args[1]
        self.assertEqual(context["document_screening"]["screening_completeness"], completeness)
        self.assertTrue(run["stage_outputs"]["spatial_screening"]["human_review_required"])
        self.assertEqual(run["errors"], [])

    def test_stage_model_configs_override_default(self):
        intake_model = self.config_path.parent / "intake.yaml"
        self.config_path.write_text(
            CONFIG + "model_configs:\n"
            f"  case_intake: {intake_model}\n"
            "  document_screening: environment/model_configs/document.yaml\n"
            "  spatial_screening: null\n",
            encoding="utf-8",
        )

        run = self.run_consultation()

        self.assertEqual(run["errors"], [])
        self.assertEqual(self.mocks["intake"].call_args.kwargs["model_config_path"], intake_model)
        self.assertEqual(
            self.mocks["document"].call_args.args[2],
            Path(__file__).resolve().parents[1] / "environment/model_configs/document.yaml",
        )
        self.assertEqual(self.mocks["spatial"].call_args.args[2], "model.yaml")

    def test_invalid_stage_model_config_stops_before_intake(self):
        self.config_path.write_text(CONFIG + "model_configs:\n  case_intake: 123\n", encoding="utf-8")

        run = self.run_consultation()

        self.assertEqual(run["errors"][0]["stage"], "configuration")
        self.mocks["intake"].assert_not_called()

    def test_intake_error_is_recorded_and_stops_flow(self):
        self.mocks["intake"].side_effect = ValueError("Bad email")

        run = self.run_consultation()

        self.assertEqual(run["errors"][0]["stage"], "case_intake")
        self.mocks["document"].assert_not_called()
        self.mocks["decision"].assert_not_called()

    def test_save_failure_is_recorded_and_run_is_returned(self):
        self.mocks["save"].side_effect = RuntimeError("S3 unavailable")

        run = self.run_consultation()

        self.assertEqual(run["errors"][-1]["stage"], "persistence")
        self.assertIn("S3 unavailable", run["errors"][-1]["message"])
        self.assertEqual(run["stage_outputs"]["decision"], {"outcome": "no_objection"})

    def test_rejects_empty_bucket(self):
        with self.assertRaisesRegex(ValueError, "bucket"):
            self.run_consultation(bucket=" ")


if __name__ == "__main__":
    unittest.main()
