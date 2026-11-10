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

    def test_document_error_is_saved_and_spatial_screening_continues(self):
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
        spatial_screening.assert_called_once()
        self.assertEqual(
            result["stage_outputs"]["spatial_screening"], {"answers": []}
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
        self.assertEqual(result["warnings"][0]["stage"], "spatial_screening")

    def test_single_lpa_polygon_ring_is_passed_to_spatial_screening(self):
        ring = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]
        case_data = {
            "reference_id": "26/00123/FUL",
            "lpa_website": "https://planning.example.org",
        }
        with (
            patch(
                "orchestrator.orchestrator.run_document_screening",
                return_value={"metadata": {"polygon": [ring]}, "final_output": {}},
            ),
            patch(
                "orchestrator.orchestrator.run_irz_code_spatial_screening",
                return_value={"answers": []},
            ) as spatial_screening,
        ):
            run_consultation(
                case_data, "model.yaml", self.output_directory, run_id="run-4"
            )

        self.assertEqual(spatial_screening.call_args.kwargs["polygon"], ring)

    def test_multiple_lpa_polygon_rings_skip_spatial_with_one_warning(self):
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
        self.assertEqual(len(result["warnings"]), 1)


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