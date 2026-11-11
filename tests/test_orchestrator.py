import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from backend.src.ai_triage.api import create_app
from orchestrator.orchestrator import run_consultation


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.output_directory = Path(self.temporary_directory.name) / "run"

    def test_missing_required_fields_stop_before_document_screening(self):
        with patch(
            "orchestrator.orchestrator.run_document_screening"
        ) as document_screening:
            result = run_consultation(
                {}, "model.yaml", self.output_directory, run_id="run-1"
            )

        document_screening.assert_not_called()
        self.assertIn("reference_id", result["errors"][0]["message"])
        self.assertIn("lpa_website", result["errors"][0]["message"])
        self.assertTrue((self.output_directory / "run.json").is_file())

    def test_required_fields_are_loaded_from_orchestrator_config(self):
        config_path = Path(self.temporary_directory.name) / "orchestrator.yaml"
        config_path.write_text(
            "required_fields:\n"
            "  case_intake: [configured_field]\n"
            "  document_screening: [polygon]\n"
            "  spatial_screening: [polygon]\n",
            encoding="utf-8",
        )
        case_data = {
            "reference_id": "26/00123/FUL",
            "lpa_website": "https://planning.example.org",
        }
        with (
            patch("orchestrator.orchestrator._DEFAULT_ORCHESTRATOR_CONFIG", config_path),
            patch("orchestrator.orchestrator.run_case_intake", return_value=case_data),
            patch("orchestrator.orchestrator.run_document_screening") as document_screening,
        ):
            result = run_consultation(case_data, "model.yaml", self.output_directory)

        document_screening.assert_not_called()
        self.assertEqual(result["errors"][0]["stage"], "case_intake")
        self.assertIn("configured_field", result["errors"][0]["message"])

    def test_document_error_is_saved_and_stops_before_spatial_screening(self):
        polygon = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]
        case_data = {
            "reference_id": "26/00123/FUL",
            "lpa_website": "https://planning.example.org",
            "polygon": polygon,
        }
        with (
            patch(
                "orchestrator.orchestrator.run_document_screening",
                side_effect=RuntimeError("document service unavailable"),
            ),
            patch(
                "orchestrator.orchestrator.run_irz_code_spatial_screening",
                return_value={"answers": []},
            ) as spatial_screening,
        ):
            result = run_consultation(
                case_data, "model.yaml", self.output_directory, run_id="run-2"
            )

        self.assertEqual(result["errors"][0]["stage"], "document_screening")
        spatial_screening.assert_not_called()
        self.assertEqual(
            result["stage_outputs"]["spatial_screening"], {}
        )
        saved_run = json.loads((self.output_directory / "run.json").read_text())
        self.assertEqual(saved_run, result)

    def test_spatial_screening_is_skipped_when_no_polygon_is_available(self):
        case_data = {
            "reference_id": "26/00123/FUL",
            "lpa_website": "https://planning.example.org",
        }
        with (
            patch(
                "orchestrator.orchestrator.run_document_screening",
                return_value={"metadata": {"polygon": []}},
            ),
            patch(
                "orchestrator.orchestrator.run_irz_code_spatial_screening"
            ) as spatial_screening,
        ):
            result = run_consultation(
                case_data, "model.yaml", self.output_directory, run_id="run-3"
            )

        spatial_screening.assert_not_called()
        self.assertEqual(result["errors"][0]["stage"], "document_screening")

    def test_single_lpa_polygon_ring_is_passed_to_spatial_screening(self):
        ring = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]
        case_data = {
            "reference_id": "26/00123/FUL",
            "lpa_website": "https://planning.example.org",
        }
        with (
            patch(
                "orchestrator.orchestrator.run_document_screening",
                return_value={
                    "metadata": {"polygon": [ring]},
                    "final_output": {"polygon": [ring]},
                },
            ),
            patch(
                "orchestrator.orchestrator.run_irz_code_spatial_screening",
                return_value={"polygon": ring, "answers": []},
            ) as spatial_screening,
        ):
            result = run_consultation(
                case_data, "model.yaml", self.output_directory, run_id="run-4"
            )

        self.assertEqual(spatial_screening.call_args.kwargs["polygon"], ring)
        self.assertEqual(result["errors"], [])

    def test_multiple_lpa_polygon_rings_stop_before_spatial_with_one_error(self):
        ring = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]
        case_data = {
            "reference_id": "26/00123/FUL",
            "lpa_website": "https://planning.example.org",
        }
        with (
            patch(
                "orchestrator.orchestrator.run_document_screening",
                return_value={"metadata": {"polygon": [ring, ring]}},
            ),
            patch(
                "orchestrator.orchestrator.run_irz_code_spatial_screening"
            ) as spatial_screening,
        ):
            result = run_consultation(
                case_data, "model.yaml", self.output_directory, run_id="run-5"
            )

        spatial_screening.assert_not_called()
        self.assertEqual(len(result["errors"]), 1)

    def test_intake_error_is_saved_and_stops_the_flow(self):
        with (
            patch("orchestrator.orchestrator.run_case_intake", side_effect=ValueError("Bad input")),
            patch("orchestrator.orchestrator.run_document_screening") as documents,
        ):
            result = run_consultation({}, "model.yaml", self.output_directory)

        documents.assert_not_called()
        self.assertEqual(result["errors"][0]["stage"], "case_intake")
        self.assertIn("Bad input", result["errors"][0]["message"])
        self.assertEqual(json.loads((self.output_directory / "run.json").read_text()), result)
        self.assertEqual(len(result["run_id"]), 32)

    def test_stage_validation_and_spatial_failures_return_saved_partial_report(self):
        case_intake = {"reference_id": "new-reference", "lpa_website": "https://planning.example.org"}
        ring = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]
        document_result = {"final_output": {"polygon": [ring]}}
        scenarios = [
            (None, None, None, "case_intake"),
            (case_intake, None, None, "document_screening"),
            (case_intake, {"unsupported_value": object()}, None, "document_screening"),
            (case_intake, document_result, RuntimeError("IRZ unavailable"), "spatial_screening"),
            (case_intake, document_result, {"polygon": []}, "spatial_screening"),
            (case_intake, document_result, {"polygon": ring}, None),
        ]
        for intake, documents, spatial, expected_error in scenarios:
            with self.subTest(expected_error=expected_error, spatial=spatial):
                with (
                    patch("orchestrator.orchestrator.run_case_intake", return_value=intake),
                    patch("orchestrator.orchestrator.run_document_screening", return_value=documents) as document_call,
                    patch("orchestrator.orchestrator.run_irz_code_spatial_screening") as spatial_call,
                ):
                    if isinstance(spatial, Exception):
                        spatial_call.side_effect = spatial
                    else:
                        spatial_call.return_value = spatial
                    result = run_consultation({}, "model.yaml", self.output_directory)

                self.assertEqual(json.loads((self.output_directory / "run.json").read_text()), result)
                if expected_error is None:
                    self.assertEqual(result["errors"], [])
                    self.assertEqual(document_call.call_args.kwargs["reference"], "new-reference")
                    self.assertEqual(spatial_call.call_args.kwargs["polygon"], ring)
                    self.assertEqual(spatial_call.call_args.kwargs["context"]["case_data"], intake)
                else:
                    self.assertEqual(result["errors"][0]["stage"], expected_error)
                if expected_error in {"case_intake", "document_screening"}:
                    spatial_call.assert_not_called()

    def test_completed_stages_are_saved_before_the_next_stage_starts(self):
        case_data = {"reference_id": "reference", "lpa_website": "https://planning.example.org"}
        ring = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]
        document_result = {"final_output": {"polygon": [ring]}}

        def screen_documents(**kwargs):
            saved = json.loads((self.output_directory / "run.json").read_text())
            self.assertEqual(saved["stage_outputs"]["case_intake"], case_data)
            return document_result

        def screen_spatially(**kwargs):
            saved = json.loads((self.output_directory / "run.json").read_text())
            self.assertEqual(saved["stage_outputs"]["document_screening"], document_result)
            return {"polygon": ring}

        with (
            patch("orchestrator.orchestrator.run_document_screening", side_effect=screen_documents),
            patch("orchestrator.orchestrator.run_irz_code_spatial_screening", side_effect=screen_spatially),
        ):
            result = run_consultation(case_data, "model.yaml", self.output_directory)

        self.assertEqual(result["errors"], [])

    def test_save_failure_is_returned_without_starting_document_screening(self):
        self.output_directory.write_text("Not a directory", encoding="utf-8")
        case_data = {"reference_id": "reference", "lpa_website": "https://planning.example.org"}
        with patch("orchestrator.orchestrator.run_document_screening") as documents:
            result = run_consultation(case_data, "model.yaml", self.output_directory)

        documents.assert_not_called()
        self.assertEqual(result["errors"][0]["stage"], "persistence")


class ConsultationApiTests(unittest.TestCase):
    def test_post_creates_run_and_get_retrieves_it(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            model_config_path = root / "model.yaml"
            model_config_path.write_text("provider: test\n", encoding="utf-8")
            app = create_app(
                model_config_path=model_config_path,
                output_root=root / "runs",
            )

            def save_fake_run(case_data, model_config_path, output_directory, run_id):
                result = {"run_id": run_id, "stage_outputs": {"email_intake": case_data}}
                output_directory.mkdir(parents=True)
                (output_directory / "run.json").write_text(
                    json.dumps(result), encoding="utf-8"
                )
                return result

            async def make_requests():
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    created = await client.post(
                        "/consultations",
                        json={"reference_id": "26/00123/FUL"},
                    )
                    retrieved = await client.get(
                        f"/consultations/{created.json()['run_id']}"
                    )
                    return created, retrieved

            with patch(
                "backend.src.ai_triage.api.run_consultation",
                side_effect=save_fake_run,
            ):
                created, retrieved = asyncio.run(make_requests())

        self.assertEqual(created.status_code, 200)
        self.assertEqual(retrieved.status_code, 200)
        self.assertEqual(retrieved.json(), created.json())


if __name__ == "__main__":
    unittest.main()