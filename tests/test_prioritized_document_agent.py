import json
import io
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.src.document_screening.implementations.prioritized_document_agent import (
    _load_implementation_config,
    _parse_prioritization,
    run_prioritized_document_screening,
)
from backend.src.utilities.document_parser import ParsedPage
from document_screening_fixtures import valid_document_findings


class PrioritizedDocumentAgentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.output_directory = self.root / "case"
        self.output_directory.mkdir()
        self.metadata = {
            "reference": "25/03341/CDPN",
            "id": "KEY",
            "summary": {"Proposal": "Conversion of an office to a dwelling"},
            "further_information": {"Ward": "North"},
            "polygon": [],
            "documents": [
                {
                    "id": "11", "published": "20 Oct 2026", "type": "Statement",
                    "drawing_number": None, "description": "Planning statement",
                    "url": "https://planning.test/files/11.pdf",
                },
                {
                    "id": "12", "published": "21 Oct 2026", "type": "Report",
                    "drawing_number": "ECO-1", "description": "Ecology report",
                    "url": "https://planning.test/files/12.pdf",
                },
                {
                    "id": "13", "published": "22 Oct 2026", "type": "Photo",
                    "drawing_number": None, "description": "Photographs",
                    "url": "https://planning.test/files/13.pdf",
                },
            ],
        }
        self.prioritization = [
            {"id": "11", "priority": "must", "uncertain": False, "reason": "Planning statement."},
            {"id": "12", "priority": "maybe", "uncertain": False, "reason": "May contain site evidence."},
            {"id": "13", "priority": "skip", "uncertain": False, "reason": "Photographs are in the skip list."},
        ]
        self.findings = valid_document_findings()
        self.lpa = Mock()
        self.lpa.search.return_value = "KEY"
        self.lpa.get_metadata.return_value = self.metadata
        self.lpa.download_documents_concurrently.side_effect = self._download_selected
        self.llm = Mock()
        self.llm.config = SimpleNamespace(
            max_output_tokens=1500, provider="ollama", model="vision-model"
        )
        self.llm.generate.side_effect = [
            json.dumps({"documents": self.prioritization}),
            json.dumps(self.findings),
        ]
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        self.lpa_factory = contexts.enter_context(patch(
            "backend.src.document_screening.implementations.prioritized_document_agent.PublicAccessClient",
            return_value=self.lpa,
        ))
        self.llm_factory = contexts.enter_context(patch(
            "backend.src.document_screening.implementations.prioritized_document_agent.LLMClient"
        ))
        self.llm_factory.return_value.__enter__.return_value = self.llm
        self.pages = [ParsedPage(
            self.output_directory / "11.pdf",
            1,
            "Evidence on page one",
            self.output_directory / "rendered-pages" / "11-page-0001.png",
        )]
        self.parser = contexts.enter_context(patch(
            "backend.src.document_screening.implementations.prioritized_document_agent.parse_pdf",
            return_value=self.pages,
        ))

    def _download_selected(self, keyval, metadata, directory, document_ids, *, max_workers):
        for document in metadata["documents"]:
            if document["id"] in document_ids:
                document["downloaded"] = True
                document["saved_as"] = f"{document['id']}.pdf"
        return metadata

    def _write_config(
        self,
        *,
        include_maybe=False,
        max_workers=2,
        default_polygon_area_square_metres=50_000,
    ):
        config_path = self.root / "implementation.yaml"
        config_path.write_text(
            "document_screening:\n"
            f"  max_concurrent_downloads: {max_workers}\n"
            f"  include_maybe: {str(include_maybe).lower()}\n"
            f"  default_polygon_area_square_metres: {default_polygon_area_square_metres}\n"
            "lpa:\n"
            "  request_interval_seconds: 0\n"
            "  retry_increment_seconds: 0\n"
            "  max_retries: 0\n"
            "  max_wait_seconds: 60\n"
            "  refresh_session_on_429: true\n"
            "  session_refresh_wait_seconds: 60\n"
            "  max_session_refreshes: 3\n",
            encoding="utf-8",
        )
        return config_path

    def run_agent(self, config_path, *, ignore_after=None):
        return run_prioritized_document_screening(
            reference="25/03341/CDPN",
            base_url="https://planning.test/online-applications/",
            model_config_path="model.yaml",
            output_directory=self.output_directory,
            implementation_config_path=config_path,
            ignore_after=ignore_after,
        )

    def test_config_parses_defaults_without_ignore_after(self):
        config_path = self._write_config()
        self.assertEqual(_load_implementation_config(config_path), {
            "max_concurrent_downloads": 2,
            "include_maybe": False,
            "default_polygon_area_square_metres": 50_000.0,
            "lpa_settings": {
                "request_interval_seconds": 0,
                "retry_increment_seconds": 0,
                "max_retries": 0,
                "max_wait_seconds": 60,
                "refresh_session_on_429": True,
                "session_refresh_wait_seconds": 60,
                "max_session_refreshes": 3,
            },
        })

    def test_prioritization_uncertainty_forces_must_and_invalid_output_raises(self):
        uncertain = [{
            "id": "11", "priority": "skip", "uncertain": True,
            "reason": "The metadata is ambiguous.",
        }]
        parsed = _parse_prioritization(json.dumps({"documents": uncertain}), ["11"])
        self.assertEqual(parsed[0]["priority"], "must")
        with self.assertRaisesRegex(ValueError, "missing IDs"):
            _parse_prioritization(json.dumps({"documents": uncertain}), ["11", "12"])

    def test_rank_download_extract_and_screen_only_must_documents(self):
        config_path = self._write_config(
            default_polygon_area_square_metres=25_000
        )

        progress = io.StringIO()
        with redirect_stdout(progress):
            result = self.run_agent(config_path)

        self.lpa.get_metadata.assert_called_once_with(
            "KEY", self.output_directory, ignore_after=None
        )
        self.lpa.download_documents_concurrently.assert_called_once_with(
            "KEY", self.metadata, self.output_directory, ["11"], max_workers=2
        )
        self.assertEqual(
            [document["priority"] for document in result["metadata"]["documents"]],
            ["must", "maybe", "skip"],
        )
        self.assertEqual(
            [document["downloaded"] for document in result["metadata"]["documents"]],
            [True, False, False],
        )
        self.assertEqual(self.parser.call_count, 1)
        self.llm.generate.assert_has_calls([
            unittest.mock.call(
                unittest.mock.ANY,
                system_prompt=unittest.mock.ANY,
            ),
            unittest.mock.call(
                unittest.mock.ANY,
                system_prompt=unittest.mock.ANY,
                attachments=[self.pages[0].image_path],
            ),
        ])
        self.assertIn("11.pdf", self.llm.generate.call_args.args[0])
        self.assertNotIn(
            "https://planning.test/files/11.pdf",
            self.llm.generate.call_args.args[0],
        )
        self.assertEqual(result["final_output"]["location"], self.findings["location"])
        self.assertEqual(
            result["final_output"]["polygon"]["polygon_source"],
            "document_coordinates",
        )
        self.assertTrue((self.output_directory / "KEY-prioritized-final-output.json").exists())
        self.assertIn("Starting document ranking", progress.getvalue())
        self.assertIn("Completed downloads: 1/1", progress.getvalue())
        self.assertIn("Completed output save", progress.getvalue())

    def test_ignore_after_is_passed_as_a_function_parameter(self):
        cutoff = date(2026, 10, 21)

        self.run_agent(self._write_config(), ignore_after=cutoff)

        self.lpa.get_metadata.assert_called_once_with(
            "KEY", self.output_directory, ignore_after=cutoff
        )

    def test_ignore_after_is_not_a_supported_config_setting(self):
        config_path = self._write_config()
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace(
                "  include_maybe: false\n",
                "  include_maybe: false\n  ignore_after: '2026-10-21'\n",
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "only:"):
            _load_implementation_config(config_path)

    def test_string_coordinates_and_missing_site_area_produce_low_confidence_default_polygon(self):
        self.findings["location"]["coordinates"]["easting"] = "359926"
        self.findings["location"]["coordinates"]["northing"] = "322794"
        self.findings["location"]["site_area"] = {
            "value": None,
            "status": "not_stated",
            "confidence": "high",
            "reason": "No site area was stated.",
            "evidence": [],
        }
        self.llm.generate.side_effect = [
            json.dumps({"documents": self.prioritization}),
            json.dumps(self.findings),
        ]
        config_path = self._write_config(default_polygon_area_square_metres=25_000)

        result = self.run_agent(config_path)

        polygon = result["final_output"]["polygon"]
        self.assertEqual(polygon["polygon_source"], "document_coordinates")
        self.assertEqual(polygon["polygon_confidence"], "low")
        self.assertEqual(polygon["polygon_area_source"], "default_5_hectares")
        ring = polygon["polygon"][0]
        self.assertAlmostEqual(ring[0][0], 359926 - (25_000 ** 0.5 / 2))
        self.assertAlmostEqual(ring[0][1], 322794 - (25_000 ** 0.5 / 2))
        self.assertEqual(result["final_output"]["location"]["coordinates"]["easting"], 359926)

    def test_include_maybe_setting_adds_maybe_documents_to_downloads(self):
        config_path = self._write_config(include_maybe=True)
        self.run_agent(config_path)
        self.lpa.download_documents_concurrently.assert_called_once_with(
            "KEY", self.metadata, self.output_directory, ["11", "12"], max_workers=2
        )

    def test_ranking_failure_stops_before_download(self):
        config_path = self._write_config()
        self.llm.generate.side_effect = RuntimeError("ranking model unavailable")

        with self.assertRaisesRegex(RuntimeError, "ranking model unavailable"):
            self.run_agent(config_path)

        self.lpa.download_documents_concurrently.assert_not_called()
        self.parser.assert_not_called()
        self.assertFalse((self.output_directory / "KEY-prioritized-final-output.json").exists())


if __name__ == "__main__":
    unittest.main()
