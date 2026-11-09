import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from backend.src.spatial_screening.implementations.spatial_agent import (
    run_spatial_screening,
)
from backend.src.spatial_screening.tools.internal_irz_client import InternalIRZClient
from backend.src.spatial_screening.tools.utilities import check_strategic


class SpatialAgentTests(unittest.TestCase):
    def test_run_spatial_screening_answers_each_question(self):
        polygon = [
            [521707.989, 444919.962],
            [521710.0, 444919.962],
            [521707.989, 444919.962],
        ]
        questions = [
            {
                "category": "Water quality",
                "options": [{"value": "yes", "text": "Yes"}],
            },
            {
                "category": "Protected species",
                "options": [{"value": "no", "text": "No"}],
            },
        ]
        model_config_path = Path("model.yaml")
        context = {
            "application": {"description": "Planning application evidence"},
            "findings": ["The site is near a protected area."],
        }

        with patch("backend.src.spatial_screening.implementations.spatial_agent.InternalIRZClient") as irz_client_class, \
            patch("backend.src.spatial_screening.implementations.spatial_agent.check_strategic", return_value=[{"gis_ref": "21101"}]), \
            patch("backend.src.spatial_screening.implementations.spatial_agent.get_consultation_questions", return_value=questions), \
            patch("backend.src.spatial_screening.implementations.spatial_agent.LLMClient") as intelligence_class:
            irz_client_class.return_value.get_internal_link.return_value = (
                "https://irz.test/21101"
            )
            intelligence = intelligence_class.return_value.__enter__.return_value
            intelligence.generate.side_effect = ["Answer one", "Answer two"]

            result = run_spatial_screening(
                polygon,
                context,
                model_config_path,
            )

        irz_client_class.return_value.get_internal_link.assert_called_once_with(polygon)
        intelligence_class.assert_called_once_with(model_config_path)
        self.assertTrue(result["is_strategic_consultation"])
        self.assertEqual(result["polygon"], polygon)
        self.assertEqual(
            result["answered_questions"],
            [
                {"question": questions[0], "answer": "Answer one"},
                {"question": questions[1], "answer": "Answer two"},
            ],
        )
        self.assertEqual(intelligence.generate.call_count, 2)
        first_prompt = intelligence.generate.call_args_list[0].args[0]
        self.assertIn('"description": "Planning application evidence"', first_prompt)
        self.assertIn('"The site is near a protected area."', first_prompt)
        self.assertIn("Water quality", first_prompt)

    def test_internal_irz_client_sends_bng_coordinates_without_transforming(self):
        polygon = [
            [521707.989, 444919.962],
            [521710.0, 444919.962],
            [521707.989, 444919.962],
        ]
        client = InternalIRZClient()
        response = MagicMock()
        response.json.return_value = {"link": "https://irz.test/location"}

        with patch.object(client.session, "post", return_value=response) as post:
            link = client.get_internal_link(polygon)

        self.assertEqual(link, "https://irz.test/location")
        geojson = json.loads(json.loads(post.call_args.kwargs["data"]["data"]))
        self.assertEqual(geojson["coordinates"], [polygon])

    def test_internal_irz_client_rejects_longitude_latitude_coordinates(self):
        client = InternalIRZClient()

        with self.assertRaisesRegex(ValueError, "longitude/latitude"):
            client.get_internal_link([[-1.0, 52.0], [-1.1, 52.1]])

    def test_internal_irz_client_uses_configured_request_timeout(self):
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "spatial_screening.yaml"
            config_path.write_text(
                "internal_irz:\n  request_timeout_seconds: 12\n",
                encoding="utf-8",
            )
            client = InternalIRZClient(config_path=config_path)
            response = MagicMock()
            response.json.return_value = {"link": "https://irz.test/location"}

            with patch.object(client.session, "post", return_value=response) as post:
                client.get_internal_link([
                    [521707.989, 444919.962],
                    [521710.0, 444919.962],
                    [521707.989, 444919.962],
                ])

        self.assertEqual(post.call_args.kwargs["timeout"], 12.0)

    def test_internal_irz_client_rejects_invalid_request_timeout(self):
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "spatial_screening.yaml"
            config_path.write_text(
                "internal_irz:\n  request_timeout_seconds: 0\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "positive finite number"):
                InternalIRZClient(config_path=config_path)

    def test_check_strategic_reads_repository_data(self):
        self.assertTrue(check_strategic("21101"))

    def test_run_spatial_screening_without_questions_skips_model_connection(self):
        with patch("backend.src.spatial_screening.implementations.spatial_agent.InternalIRZClient") as irz_client_class, \
            patch("backend.src.spatial_screening.implementations.spatial_agent.check_strategic", return_value=[]), \
            patch("backend.src.spatial_screening.implementations.spatial_agent.get_consultation_questions", return_value=[]), \
            patch("backend.src.spatial_screening.implementations.spatial_agent.LLMClient") as intelligence_class:
            irz_client_class.return_value.get_internal_link.return_value = (
                "https://irz.test/no-questions"
            )

            result = run_spatial_screening([], {}, "model.yaml")

        intelligence_class.assert_not_called()
        self.assertFalse(result["is_strategic_consultation"])
        self.assertEqual(result["answered_questions"], [])


if __name__ == "__main__":
    unittest.main()