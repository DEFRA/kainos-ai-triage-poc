import asyncio
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from backend.src.ai_triage.api import create_app
from orchestrator.orchestrator import run_consultation


def make_case_intake(
    reference_id="26/00123/FUL",
    lpa_website="https://planning.example.org",
    consultation_date=None,
):
    signal_fields = (
        "eia_screening",
        "eia_scoping",
        "conditions",
        "pre_application",
        "prior_approval",
        "minerals_waste_existing",
        "minerals_waste_new",
    )
    case_intake = {
        "lpa_reference_id": {
            "value": reference_id,
            "status": "stated",
            "confidence": "high",
            "reason": "Provided by case intake.",
            "evidence": [],
        },
        "lpa_website": {
            "value": lpa_website,
            "status": "stated",
            "confidence": "high",
            "reason": "Provided by case intake.",
            "evidence": [],
        },
        "signals": {
            name: {
                "classification": "no_signal",
                "confidence": "high",
                "reason": "No signal identified.",
                "evidence": [],
            }
            for name in signal_fields
        },
    }
    if consultation_date is not None:
        case_intake["consultation_date"] = {
            "value": consultation_date,
            "status": "stated",
            "confidence": "high",
            "reason": "Provided by case intake.",
            "evidence": [],
        }
    return case_intake


def make_document_result(ring):
    signal_fields = (
        "amendment",
        "minerals_waste_existing",
        "minerals_waste_new",
    )
    return {
        "final_output": {
            "polygon": {
                "polygon": [ring],
                "polygon_source": "lpa_maps",
                "polygon_confidence": "high",
                "polygon_area_source": "lpa_polygon",
            },
            "consultation_type_signals": {
                name: {
                    "classification": "no_signal",
                    "confidence": "high",
                    "reason": "No signal identified.",
                    "evidence": [],
                }
                for name in signal_fields
            },
        },
    }


def make_spatial_result(ring):
    return {
        "polygon": ring,
        "geospatial_checks": {
            "best_and_most_versatile_land": {
                "intersects": False,
                "overlap_area_hectares": 0.0,
            },
        },
        "answers": [{
            "category": "All Consultations",
            "value": 0,
            "text": "Not applicable",
            "confidence": "High",
            "reasoning": "No applicable category identified.",
            "supporting_evidence": [],
        }],
    }


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
        self.assertIn("lpa_reference_id", result["errors"][0]["message"])
        self.assertIn("lpa_website", result["errors"][0]["message"])
        self.assertTrue((self.output_directory / "run.json").is_file())

    def test_required_fields_are_loaded_from_orchestrator_config(self):
        config_path = Path(self.temporary_directory.name) / "orchestrator.yaml"
        config_path.write_text(
            "required_fields:\n"
            "  case_intake: [configured_field]\n"
            "  document_screening: [polygon]\n"
            "  spatial_screening: [polygon]\n"
            "  decision: [classification, checklist]\n",
            encoding="utf-8",
        )
        case_data = make_case_intake()
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
        case_data = make_case_intake()
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
        case_data = make_case_intake()
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
        case_data = make_case_intake()
        with (
            patch(
                "orchestrator.orchestrator.run_document_screening",
                return_value=make_document_result(ring),
            ),
            patch(
                "orchestrator.orchestrator.run_irz_code_spatial_screening",
                return_value=make_spatial_result(ring),
            ) as spatial_screening,
        ):
            result = run_consultation(
                case_data, "model.yaml", self.output_directory, run_id="run-4"
            )

        self.assertEqual(spatial_screening.call_args.kwargs["polygon"], ring)
        self.assertEqual(
            result["stage_outputs"]["document_screening"]["final_output"]["polygon"],
            {
                "polygon": [ring],
                "polygon_source": "lpa_maps",
                "polygon_confidence": "high",
                "polygon_area_source": "lpa_polygon",
            },
        )
        self.assertEqual(result["errors"], [])

    def test_multiple_lpa_polygon_rings_stop_before_spatial_with_one_error(self):
        ring = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]
        case_data = make_case_intake()
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
        case_intake = make_case_intake(reference_id="new-reference")
        ring = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]
        document_result = make_document_result(ring)
        scenarios = [
            (None, None, None, "case_intake"),
            (case_intake, None, None, "document_screening"),
            (case_intake, {"unsupported_value": object()}, None, "document_screening"),
            (case_intake, document_result, RuntimeError("IRZ unavailable"), "spatial_screening"),
            (case_intake, document_result, {"polygon": []}, "spatial_screening"),
            (case_intake, document_result, make_spatial_result(ring), None),
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

    def test_document_polygon_is_forwarded_to_spatial_screening(self):
        case_data = make_case_intake(reference_id="reference")
        ring = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]
        document_result = make_document_result(ring)

        with (
            patch(
                "orchestrator.orchestrator.run_document_screening",
                return_value=document_result,
            ) as document_screening,
            patch(
                "orchestrator.orchestrator.run_irz_code_spatial_screening",
                return_value=make_spatial_result(ring),
            ) as spatial_screening,
        ):
            result = run_consultation(case_data, "model.yaml", self.output_directory)

        self.assertEqual(result["errors"], [])
        self.assertEqual(spatial_screening.call_args.kwargs["polygon"], ring)
        self.assertEqual(
            spatial_screening.call_args.kwargs["context"]["document_screening"]["polygon"],
            document_result["final_output"]["polygon"],
        )
        self.assertNotIn("ignore_after", document_screening.call_args.kwargs)

    def test_decision_stage_receives_adapted_outputs_and_saves_result(self):
        case_intake = make_case_intake(reference_id="reference")
        case_intake["signals"]["conditions"] = {
            "classification": "yes",
            "confidence": "high",
            "reason": "A condition is referenced.",
            "evidence": [{"source": "intake email", "quote": "Condition 3"}],
        }
        ring = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]
        document_result = make_document_result(ring)
        document_signals = document_result["final_output"]["consultation_type_signals"]
        document_signals["amendment"]["classification"] = "yes"
        document_signals["amendment"]["evidence"] = [{
            "document_id": "APP-1",
            "page": 2,
            "quote": "Revised application documents",
        }]
        spatial_result = make_spatial_result(ring)
        spatial_result["geospatial_checks"]["best_and_most_versatile_land"].update(
            intersects=True, overlap_area_hectares=23.4,
        )
        decision_result = {
            "classification": "necessary",
            "confidence": "high",
            "checklist": [{"guidance_id": "1c", "answer": True}],
        }

        with (
            patch(
                "orchestrator.orchestrator.run_document_screening",
                return_value=document_result,
            ),
            patch(
                "orchestrator.orchestrator.run_irz_code_spatial_screening",
                return_value=spatial_result,
            ),
            patch(
                "orchestrator.orchestrator.run_decision",
                return_value=decision_result,
            ) as decision,
        ):
            result = run_consultation(case_intake, "model.yaml", self.output_directory)

        decision.assert_called_once_with({
            "case_intake": case_intake["signals"],
            "document_screening": {
                "amendment": document_signals["amendment"],
                "minerals_waste_existing": document_signals["minerals_waste_existing"],
                "minerals_waste_new": document_signals["minerals_waste_new"],
            },
            "spatial_screening": {
                "best_and_most_versatile_land": spatial_result["geospatial_checks"]["best_and_most_versatile_land"],
                "answers": spatial_result["answers"],
            },
        })
        self.assertEqual(result["stage_outputs"]["decision"], decision_result)
        self.assertEqual(result["errors"], [])
        self.assertEqual(
            json.loads((self.output_directory / "run.json").read_text()),
            result,
        )

    def test_spatial_decision_inputs_are_required_before_decision(self):
        case_intake = make_case_intake()
        ring = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]
        for missing_field in ("best_and_most_versatile_land", "answers"):
            with self.subTest(missing_field=missing_field):
                spatial_result = make_spatial_result(ring)
                if missing_field == "best_and_most_versatile_land":
                    del spatial_result["geospatial_checks"][missing_field]
                else:
                    del spatial_result[missing_field]
                with (
                    patch(
                        "orchestrator.orchestrator.run_document_screening",
                        return_value=make_document_result(ring),
                    ),
                    patch(
                        "orchestrator.orchestrator.run_irz_code_spatial_screening",
                        return_value=spatial_result,
                    ),
                    patch("orchestrator.orchestrator.run_decision") as decision,
                ):
                    result = run_consultation(
                        case_intake, "model.yaml", self.output_directory
                    )

                decision.assert_not_called()
                self.assertEqual(result["errors"][0]["stage"], "spatial_screening")
                self.assertIn(missing_field, result["errors"][0]["message"])

    def test_decision_error_is_saved_after_spatial_screening(self):
        case_intake = make_case_intake()
        ring = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]
        with (
            patch(
                "orchestrator.orchestrator.run_document_screening",
                return_value=make_document_result(ring),
            ),
            patch(
                "orchestrator.orchestrator.run_irz_code_spatial_screening",
                return_value=make_spatial_result(ring),
            ) as spatial,
            patch(
                "orchestrator.orchestrator.run_decision",
                side_effect=ValueError("Invalid decision evidence"),
            ),
        ):
            result = run_consultation(case_intake, "model.yaml", self.output_directory)

        spatial.assert_called_once()
        self.assertEqual(result["errors"][0]["stage"], "decision")
        self.assertEqual(result["stage_outputs"]["decision"], {})
        self.assertEqual(json.loads((self.output_directory / "run.json").read_text()), result)

    def test_stated_consultation_date_is_passed_as_ignore_after(self):
        case_data = make_case_intake(consultation_date="2026-10-21")
        ring = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]
        document_result = make_document_result(ring)
        with (
            patch(
                "orchestrator.orchestrator.run_document_screening",
                return_value=document_result,
            ) as document_screening,
            patch(
                "orchestrator.orchestrator.run_irz_code_spatial_screening",
                return_value=make_spatial_result(ring),
            ),
        ):
            result = run_consultation(case_data, "model.yaml", self.output_directory)

        self.assertEqual(result["errors"], [])
        self.assertEqual(
            document_screening.call_args.kwargs["ignore_after"],
            date(2026, 10, 21),
        )

    def test_malformed_stated_consultation_date_stops_at_case_intake(self):
        case_data = make_case_intake(consultation_date="21/10/2026")
        with patch(
            "orchestrator.orchestrator.run_document_screening"
        ) as document_screening:
            result = run_consultation(case_data, "model.yaml", self.output_directory)

        document_screening.assert_not_called()
        self.assertEqual(result["errors"][0]["stage"], "case_intake")
        self.assertIn("YYYY-MM-DD", result["errors"][0]["message"])

    def test_document_result_without_final_output_is_rejected(self):
        case_data = make_case_intake()
        ring = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]
        with (
            patch(
                "orchestrator.orchestrator.run_document_screening",
                return_value={"metadata": {"polygon": [ring]}},
            ),
            patch(
                "orchestrator.orchestrator.run_irz_code_spatial_screening",
                return_value={"polygon": ring},
            ) as spatial_screening,
        ):
            result = run_consultation(case_data, "model.yaml", self.output_directory)

        self.assertEqual(result["errors"][0]["stage"], "document_screening")
        self.assertIn("final_output", result["errors"][0]["message"])
        spatial_screening.assert_not_called()

    def test_final_output_rejects_raw_polygon_rings(self):
        case_data = make_case_intake()
        ring = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]
        with (
            patch(
                "orchestrator.orchestrator.run_document_screening",
                return_value={"final_output": {"polygon": [ring]}},
            ),
            patch(
                "orchestrator.orchestrator.run_irz_code_spatial_screening"
            ) as spatial_screening,
        ):
            result = run_consultation(case_data, "model.yaml", self.output_directory)

        self.assertEqual(result["errors"][0]["stage"], "document_screening")
        self.assertIn("polygon must be an object", result["errors"][0]["message"])
        spatial_screening.assert_not_called()

    def test_final_save_failure_is_returned_after_spatial_screening(self):
        self.output_directory.write_text("Not a directory", encoding="utf-8")
        case_data = make_case_intake(reference_id="reference")
        ring = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]
        with (
            patch(
                "orchestrator.orchestrator.run_document_screening",
                return_value=make_document_result(ring),
            ),
            patch(
                "orchestrator.orchestrator.run_irz_code_spatial_screening",
                return_value=make_spatial_result(ring),
            ) as spatial_screening,
        ):
            result = run_consultation(case_data, "model.yaml", self.output_directory)

        spatial_screening.assert_called_once()
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