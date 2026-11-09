import json
import io
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend.src.document_screening.implementations.document_agent import run_document_screening
from backend.src.utilities.document_parser import ParsedPage


class DocumentAgentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output_directory = Path(self.directory.name).resolve()
        self.metadata = {
            "reference": "25/03341/CDPN",
            "id": "KEY",
            "summary": {"Proposal": "Conversion of an office to a dwelling"},
            "further_information": {"Ward": "North"},
            "polygon": [],
            "documents": [
                {
                    "id": "123", "url": "https://planning.test/files/123.pdf",
                    "saved_as": "application.pdf", "description": "Statement",
                },
                {
                    "id": "456", "url": "https://planning.test/files/456.jpg",
                    "saved_as": "site.jpg", "description": "Site plan",
                },
            ],
        }
        self.lpa = Mock()
        self.lpa.search.return_value = "KEY"
        self.lpa.get_metadata.return_value = self.metadata
        self.document_findings = {
            "completed_assessments": [],
            "planning_permissions": [],
            "location": {"coordinates": [], "addresses": []},
            "other": [],
            "limitations": [],
        }
        self.llm = Mock()
        self.llm.config = SimpleNamespace(
            max_output_tokens=1500, provider="ollama", model="vision-model"
        )
        self.llm.generate.return_value = (
            "```json\n" + json.dumps(self.document_findings) + "\n```"
        )
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        self.lpa_factory = contexts.enter_context(patch(
            "document_screening.implementations.document_agent.PublicAccessClient",
            return_value=self.lpa,
        ))
        self.llm_factory = contexts.enter_context(patch(
            "document_screening.implementations.document_agent.LLMClient"
        ))
        self.llm_factory.return_value.__enter__.return_value = self.llm
        self.pages = [
            ParsedPage(
                self.output_directory / "application.pdf", 1, "First page evidence",
                self.output_directory / "rendered-pages" / "application-page-0001.png",
            ),
            ParsedPage(
                self.output_directory / "application.pdf", 2, "",
                self.output_directory / "rendered-pages" / "application-page-0002.png",
            ),
        ]
        self.parser = contexts.enter_context(patch(
            "document_screening.implementations.document_agent.parse_pdf",
            return_value=self.pages,
        ))

    def run_agent(self, **kwargs):
        return run_document_screening(
            reference="25/03341/CDPN",
            base_url="https://planning.test/online-applications/",
            model_config_path="model.yaml",
            output_directory=self.output_directory,
            **kwargs,
        )

    def test_runs_full_pipeline_and_sends_all_evidence_in_one_request(self):
        progress = io.StringIO()
        with redirect_stdout(progress):
            result = self.run_agent(dpi=200)

        self.lpa_factory.assert_called_once_with(
            base_url="https://planning.test/online-applications/"
        )
        self.lpa.search.assert_called_once_with("25/03341/CDPN")
        self.lpa.get_metadata.assert_called_once_with(
            "KEY", self.output_directory, ignore_after=None
        )
        self.lpa.download_documents.assert_called_once_with(
            "KEY", self.metadata, self.output_directory
        )
        self.lpa.close.assert_called_once_with()
        self.parser.assert_called_once_with(
            self.output_directory / "application.pdf",
            self.output_directory / "rendered-pages",
            dpi=200,
        )
        self.llm.generate.assert_called_once()
        call = self.llm.generate.call_args
        self.assertEqual(call.kwargs["attachments"], [
            self.pages[0].image_path,
            self.pages[1].image_path,
            self.output_directory / "site.jpg",
        ])
        self.assertIn("First page evidence", call.args[0])
        self.assertIn("Conversion of an office", call.args[0])
        self.assertNotIn("https://planning.test/files/123.pdf", call.args[0])
        self.assertIn("not a final consultation decision", call.kwargs["system_prompt"])
        self.assertIn("Starting application search", progress.getvalue())
        self.assertIn("Completed document downloads", progress.getvalue())
        self.assertIn("Completed output save", progress.getvalue())
        self.assertEqual(result["sources"][0]["pages"][1], {
            "page_number": 2, "text": "", "attachment_number": 2,
        })
        self.assertEqual(result["sources"][1]["attachment_number"], 3)
        self.assertEqual(result["metadata"], self.metadata)
        self.assertEqual(result["document_findings"], self.document_findings)
        self.assertEqual(result["final_output"], {
            **self.metadata,
            **self.document_findings,
        })
        self.assertEqual(
            result["response"],
            "```json\n" + json.dumps(self.document_findings) + "\n```",
        )
        result_path = self.output_directory / "KEY-document-screening.json"
        self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), result)
        final_output_path = self.output_directory / "KEY-final-output.json"
        self.assertEqual(
            json.loads(final_output_path.read_text(encoding="utf-8")),
            result["final_output"],
        )
        self.llm_factory.return_value.__exit__.assert_called_once()

    def test_ignore_after_is_passed_to_metadata_collection(self):
        cutoff = date(2026, 10, 21)

        self.run_agent(ignore_after=cutoff)

        self.lpa.get_metadata.assert_called_once_with(
            "KEY", self.output_directory, ignore_after=cutoff
        )

    def test_custom_prompt_json_uses_the_same_output_contract(self):
        prompt_path = self.output_directory / "custom.md"
        prompt_path.write_text("Return the document findings as JSON.", encoding="utf-8")
        custom_output = self.document_findings
        self.llm.generate.return_value = json.dumps(custom_output)

        result = self.run_agent(prompt_path=prompt_path)

        self.assertEqual(
            self.llm.generate.call_args.kwargs["system_prompt"],
            "Return the document findings as JSON.",
        )
        self.assertEqual(result["document_findings"], custom_output)
        self.assertEqual(result["final_output"]["location"], {"coordinates": [], "addresses": []})

    def test_invalid_json_does_not_save_a_success_result(self):
        self.llm.generate.return_value = "Not JSON"

        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            self.run_agent()

        self.llm_factory.return_value.__exit__.assert_called_once()
        self.assertFalse((self.output_directory / "KEY-document-screening.json").exists())
        self.assertFalse((self.output_directory / "KEY-final-output.json").exists())
        response_path = self.output_directory / "KEY-document-screening-response.txt"
        self.assertEqual(response_path.read_text(encoding="utf-8"), "Not JSON")

    def test_unexpected_json_shape_is_rejected_and_saved_for_inspection(self):
        self.llm.generate.return_value = json.dumps({"completed_assessments": []})

        with self.assertRaisesRegex(ValueError, "invalid top-level structure"):
            self.run_agent()

        response_path = self.output_directory / "KEY-document-screening-response.txt"
        self.assertEqual(response_path.read_text(encoding="utf-8"), self.llm.generate.return_value)
        self.assertFalse((self.output_directory / "KEY-final-output.json").exists())

    def test_non_pdf_files_are_forwarded_without_silent_filtering(self):
        self.metadata["documents"] = [{"id": "789", "saved_as": "report.docx"}]
        self.run_agent()
        self.parser.assert_not_called()
        self.assertEqual(self.llm.generate.call_args.kwargs["attachments"], [
            self.output_directory / "report.docx",
        ])

    def test_empty_document_list_still_sends_application_metadata(self):
        self.metadata["documents"] = []
        result = self.run_agent()
        self.parser.assert_not_called()
        self.assertEqual(result["sources"], [])
        self.assertEqual(self.llm.generate.call_args.kwargs["attachments"], [])

    def test_parser_failure_stops_before_model_request(self):
        self.parser.side_effect = ValueError("Invalid PDF")
        with self.assertRaisesRegex(ValueError, "Invalid PDF"):
            self.run_agent()
        self.llm_factory.assert_not_called()
        self.lpa.close.assert_called_once_with()
        self.assertFalse((self.output_directory / "KEY-document-screening.json").exists())

    def test_retrieval_failure_closes_lpa_client(self):
        self.lpa.get_metadata.side_effect = RuntimeError("Retrieval failed")
        with self.assertRaisesRegex(RuntimeError, "Retrieval failed"):
            self.run_agent()
        self.lpa.close.assert_called_once_with()
        self.parser.assert_not_called()
        self.llm_factory.assert_not_called()

    def test_model_failure_does_not_save_a_success_result(self):
        self.llm.generate.side_effect = RuntimeError("Context limit exceeded")
        with self.assertRaisesRegex(RuntimeError, "Context limit exceeded"):
            self.run_agent()
        self.llm_factory.return_value.__exit__.assert_called_once()
        self.assertFalse((self.output_directory / "KEY-document-screening.json").exists())

    def test_context_warning_does_not_split_or_omit_attachments(self):
        with self.assertWarnsRegex(RuntimeWarning, "excludes attachment tokens"):
            self.run_agent(context_limit=100)
        self.llm.generate.assert_called_once()
        self.assertEqual(len(self.llm.generate.call_args.kwargs["attachments"]), 3)


if __name__ == "__main__":
    unittest.main()