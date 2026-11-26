import ast
import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from docx import Document
from openpyxl import Workbook

from backend.src.document_screening.implementations.s3_document_agent import (
    run_s3_document_screening,
)
from backend.src.utilities.document_parser import ParsedPage
from document_screening_fixtures import valid_document_findings


MODULE = "backend.src.document_screening.implementations.s3_document_agent"


class S3DocumentAgentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.metadata = {
            "reference": "25/123", "id": "KEY", "summary": {},
            "further_information": {}, "polygon": [],
            "documents": [
                {"id": "11", "published": "20 Oct 2026", "type": "Statement",
                 "drawing_number": None, "description": "Planning", "url": "https://example.test/11.pdf"},
                {"id": "12", "published": "21 Oct 2026", "type": "Report",
                 "drawing_number": None, "description": "Report", "url": "https://example.test/12.pdf"},
                {"id": "13", "published": "22 Oct 2026", "type": "Photo",
                 "drawing_number": None, "description": "Photo", "url": "https://example.test/13.pdf"},
            ],
        }
        self.ranking = [
            {"id": "11", "priority": "must", "uncertain": False, "reason": "Relevant"},
            {"id": "12", "priority": "maybe", "uncertain": False, "reason": "Possible"},
            {"id": "13", "priority": "skip", "uncertain": False, "reason": "Not relevant"},
        ]
        self.findings = valid_document_findings()
        self.lpa = Mock()
        self.lpa.search.return_value = "KEY"
        self.lpa.get_metadata.side_effect = self.get_metadata
        self.lpa.download_documents_concurrently.side_effect = self.download
        self.llm = Mock()
        self.llm.config = SimpleNamespace(provider="ollama", model="vision", max_output_tokens=1500)
        self.llm.generate.side_effect = lambda *args, **kwargs: (
            json.dumps(self.findings) if "attachments" in kwargs
            else json.dumps({"documents": self.ranking})
        )
        self.objects = {}
        self.s3 = Mock()
        self.s3.put_object.side_effect = self.put_object
        self.output_directory = None
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        contexts.enter_context(patch(f"{MODULE}.PublicAccessClient", return_value=self.lpa))
        llm_factory = contexts.enter_context(patch(f"{MODULE}.LLMClient"))
        llm_factory.return_value.__enter__.return_value = self.llm
        self.parser = contexts.enter_context(patch(f"{MODULE}.parse_pdf", side_effect=self.parse_pdf))
        self.client_factory = contexts.enter_context(patch(f"{MODULE}.create_s3_client", return_value=self.s3))

    def get_metadata(self, application_key, directory, *, ignore_after):
        self.output_directory = directory
        (directory / "KEY-metadata.json").write_text(json.dumps(self.metadata), encoding="utf-8")
        return self.metadata

    def download(self, application_key, metadata, directory, document_ids, *, max_workers):
        for document in metadata["documents"]:
            if document["id"] in document_ids:
                document["downloaded"] = True
                suffix = Path(document["url"]).suffix
                document["saved_as"] = f"{document['id']}{suffix}"
                document_path = directory / document["saved_as"]
                if suffix == ".docx":
                    docx = Document()
                    docx.add_paragraph("Site area is 1.25 ha")
                    docx.save(document_path)
                elif suffix == ".xlsx":
                    workbook = Workbook()
                    workbook.active.title = "Application"
                    workbook.active["A1"] = "Site area"
                    workbook.active["B1"] = "1.25 ha"
                    workbook.save(document_path)
                else:
                    document_path.write_bytes(f"pdf-{document['id']}".encode())
        (directory / "KEY-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    def parse_pdf(self, document_path, directory, *, dpi):
        directory.mkdir(exist_ok=True)
        image_path = directory / f"{document_path.stem}-page-0001.png"
        image_path.write_bytes(b"page-image")
        return [ParsedPage(document_path, 1, "Page text", image_path)]

    def put_object(self, *, Bucket, Key, Body):
        self.objects[Key] = Body
        return {"VersionId": "version-1"}

    def run_agent(self, **kwargs):
        return run_s3_document_screening(
            "25/123", "https://example.test/", "model.yaml",
            bucket="bucket", prefix="/case/run/", **kwargs,
        )

    def test_complete_run_uploads_all_artifacts_and_preserves_output(self):
        cutoff = date(2026, 10, 21)
        result = self.run_agent(s3_client=self.s3, ignore_after=cutoff, dpi=200)
        self.client_factory.assert_not_called()
        self.lpa.get_metadata.assert_called_once_with("KEY", self.output_directory, ignore_after=cutoff)
        self.lpa.download_documents_concurrently.assert_called_once_with(
            "KEY", self.metadata, self.output_directory, ["11"], max_workers=5,
        )
        self.parser.assert_called_once_with(
            self.output_directory / "11.pdf", self.output_directory / "rendered-pages", dpi=200,
        )
        expected = {
            "11.pdf", "rendered-pages/11-page-0001.png", "KEY-metadata.json",
            "KEY-prioritized-document-findings.json",
            "KEY-prioritized-final-output.json", "KEY-prioritized-document-screening.json",
        }
        self.assertEqual(set(result["storage"]["objects"]), expected)
        self.assertEqual(self.objects["case/run/11.pdf"], b"pdf-11")
        self.assertEqual(self.objects["case/run/rendered-pages/11-page-0001.png"], b"page-image")
        self.assertEqual(
            json.loads(self.objects["case/run/KEY-prioritized-final-output.json"]),
            result["final_output"],
        )
        self.assertEqual(
            json.loads(self.objects["case/run/KEY-prioritized-document-findings.json"]),
            self.findings,
        )
        stored_result = json.loads(self.objects["case/run/KEY-prioritized-document-screening.json"])
        self.assertEqual(stored_result, {key: value for key, value in result.items() if key != "storage"})
        self.assertEqual(result["final_output"]["polygon"]["polygon_source"], "document_coordinates")
        self.assertEqual(result["sources"][0]["pages"][0]["attachment_number"], 1)
        self.assertEqual(result["storage"]["objects"]["11.pdf"]["version"], "version-1")
        self.assertEqual(self.s3.put_object.call_args.kwargs["Key"], "case/run/KEY-prioritized-document-screening.json")
        self.assertNotIn("https://example.test/11.pdf", self.llm.generate.call_args.args[0])
        self.assertFalse(self.output_directory.exists())

    def test_incomplete_development_is_preserved_in_s3_output(self):
        del self.findings["scale_and_type_of_development"]
        del self.findings["limitations"]
        self.metadata["documents"][1]["download_error"] = "Download unavailable"

        result = self.run_agent(s3_client=self.s3)

        final_output = result["final_output"]
        self.assertNotIn("scale_and_type_of_development", final_output)
        self.assertNotIn("limitations", final_output)
        self.assertEqual(final_output["processing_limitations"], [
            "Document 12 was not downloaded: Download unavailable"
        ])
        self.assertEqual(
            final_output["screening_completeness"]["incomplete_development_fields"],
            ["scale_and_type_of_development"],
        )
        stored = json.loads(self.objects["case/run/KEY-prioritized-final-output.json"])
        self.assertEqual(stored["screening_completeness"], final_output["screening_completeness"])

    def test_office_documents_are_in_source_json_not_model_attachments(self):
        self.metadata["documents"][0]["url"] = "https://example.test/11.docx"
        self.metadata["documents"][1]["url"] = "https://example.test/12.xlsx"
        self.ranking[1]["priority"] = "must"

        result = self.run_agent(s3_client=self.s3)

        prompt = self.llm.generate.call_args_list[1].args[0]
        source_data = json.loads(
            prompt.split("Application metadata and downloaded document evidence:\n", 1)[1]
            .split("\n\nAttachments follow", 1)[0]
        )
        sources = {source["id"]: source for source in source_data["sources"]}
        self.assertEqual(sources["11"]["text"], "Site area is 1.25 ha")
        self.assertEqual(sources["11"]["pages"], [])
        self.assertEqual(sources["12"]["sheets"], [{
            "name": "Application",
            "cells": [
                {"address": "A1", "value": "Site area"},
                {"address": "B1", "value": "1.25 ha"},
            ],
        }])
        self.assertEqual(self.llm.generate.call_args_list[1].kwargs["attachments"], [])
        self.assertIn("11.docx", result["storage"]["objects"])
        self.assertIn("12.xlsx", result["storage"]["objects"])
        self.assertTrue(self.objects["case/run/11.docx"].startswith(b"PK"))
        self.assertTrue(self.objects["case/run/12.xlsx"].startswith(b"PK"))

    def test_docx_extraction_failure_is_reported_and_original_is_retained(self):
        self.metadata["documents"][0]["url"] = "https://example.test/11.docx"

        def download_invalid_docx(application_key, metadata, directory, document_ids, *, max_workers):
            document = metadata["documents"][0]
            document["downloaded"] = True
            document["saved_as"] = "11.docx"
            (directory / "11.docx").write_bytes(b"not a DOCX")

        self.lpa.download_documents_concurrently.side_effect = download_invalid_docx
        result = self.run_agent(s3_client=self.s3)

        prompt = self.llm.generate.call_args_list[1].args[0]
        source_data = json.loads(
            prompt.split("Application metadata and downloaded document evidence:\n", 1)[1]
            .split("\n\nAttachments follow", 1)[0]
        )
        source = source_data["sources"][0]
        self.assertIn("could not be extracted", source["extraction_error"])
        self.assertEqual(self.llm.generate.call_args_list[1].kwargs["attachments"], [])
        self.assertTrue(any("could not be extracted" in item for item in result["document_findings"]["limitations"]))
        persisted_findings = json.loads(self.objects["case/run/KEY-prioritized-document-findings.json"])
        self.assertTrue(any("could not be extracted" in item for item in persisted_findings["limitations"]))
        self.assertEqual(self.objects["case/run/11.docx"], b"not a DOCX")

    def test_unsupported_format_is_not_attached_and_is_reported(self):
        self.metadata["documents"][0]["url"] = "https://example.test/11.doc"

        result = self.run_agent(s3_client=self.s3)

        prompt = self.llm.generate.call_args_list[1].args[0]
        source_data = json.loads(
            prompt.split("Application metadata and downloaded document evidence:\n", 1)[1]
            .split("\n\nAttachments follow", 1)[0]
        )
        source = source_data["sources"][0]
        self.assertIn("unsupported file format .doc", source["extraction_error"])
        self.assertEqual(self.llm.generate.call_args_list[1].kwargs["attachments"], [])
        self.assertTrue(any("unsupported file format .doc" in item for item in result["document_findings"]["limitations"]))

    def test_include_maybe_and_uncertain_skip_are_selected(self):
        config_path = self.root / "config.yaml"
        config_path.write_text("document_screening:\n  include_maybe: true\nlpa: {}\n", encoding="utf-8")
        self.ranking[2]["uncertain"] = True
        result = self.run_agent(implementation_config_path=config_path)
        self.client_factory.assert_called_once_with()
        self.assertEqual(self.lpa.download_documents_concurrently.call_args.args[3], ["11", "12", "13"])
        self.assertEqual(result["prioritization"][2]["priority"], "must")

    def test_download_failure_is_recorded_as_limitation(self):
        def fail_download(application_key, metadata, directory, document_ids, *, max_workers):
            metadata["documents"][0]["download_error"] = "download failed"
            (directory / "KEY-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

        self.lpa.download_documents_concurrently.side_effect = fail_download
        result = self.run_agent(s3_client=self.s3)
        self.assertIn("Document 11 was not downloaded: download failed", result["document_findings"]["limitations"])
        self.assertEqual(result["sources"], [])

    def test_screening_result_matches_local_prioritized_implementation(self):
        from backend.src.document_screening.implementations.prioritized_document_agent import (
            run_prioritized_document_screening,
        )

        original_metadata = copy.deepcopy(self.metadata)
        s3_result = self.run_agent(s3_client=self.s3)
        self.metadata = original_metadata
        local_directory = self.root / "local"
        local_directory.mkdir()
        with (
            patch("backend.src.document_screening.implementations.prioritized_document_agent.PublicAccessClient", return_value=self.lpa),
            patch("backend.src.document_screening.implementations.prioritized_document_agent.LLMClient") as llm_factory,
            patch("backend.src.document_screening.implementations.prioritized_document_agent.parse_pdf", side_effect=self.parse_pdf),
        ):
            llm_factory.return_value.__enter__.return_value = self.llm
            local_result = run_prioritized_document_screening(
                "25/123", "https://example.test/", "model.yaml", local_directory,
            )
        self.assertEqual(local_result, {key: value for key, value in s3_result.items() if key != "storage"})

    def test_ranking_failure_uploads_metadata_and_stops_before_download(self):
        self.llm.generate.side_effect = RuntimeError("ranking failed")
        with self.assertRaisesRegex(RuntimeError, "ranking failed"):
            self.run_agent(s3_client=self.s3)
        self.assertEqual(set(self.objects), {"case/run/KEY-metadata.json"})
        self.lpa.download_documents_concurrently.assert_not_called()

    def test_invalid_response_retains_partial_artifacts(self):
        self.llm.generate.side_effect = [json.dumps({"documents": self.ranking}), "not JSON"]
        with self.assertRaisesRegex(ValueError, "s3://bucket/case/run/KEY-prioritized-response.txt"):
            self.run_agent(s3_client=self.s3)
        self.assertEqual(self.objects["case/run/KEY-prioritized-response.txt"], b"not JSON")
        self.assertIn("case/run/11.pdf", self.objects)
        self.assertFalse(self.output_directory.exists())

    def test_polygon_inference_failure_uploads_parsed_document_findings(self):
        self.findings["location"]["site_area"]["value"] = "approximately 1.2 hectares"

        with self.assertRaisesRegex(ValueError, "stated site area"):
            self.run_agent(s3_client=self.s3)

        findings_key = "case/run/KEY-prioritized-document-findings.json"
        self.assertEqual(json.loads(self.objects[findings_key]), self.findings)
        self.assertIn("case/run/KEY-metadata.json", self.objects)
        self.assertFalse(self.output_directory.exists())

    def test_upload_failure_reports_key(self):
        self.s3.put_object.side_effect = OSError("S3 unavailable")
        with self.assertRaisesRegex(RuntimeError, "Could not upload artifact to s3://bucket/case/run/") as raised:
            self.run_agent(s3_client=self.s3)
        self.assertIsInstance(raised.exception.__cause__, OSError)
        self.assertFalse(self.output_directory.exists())

    def test_screening_and_upload_failures_are_both_reported(self):
        self.llm.generate.side_effect = RuntimeError("model unavailable")
        self.s3.put_object.side_effect = OSError("S3 unavailable")
        with self.assertRaises(ExceptionGroup) as raised:
            self.run_agent(s3_client=self.s3)
        self.assertEqual(str(raised.exception.exceptions[0]), "model unavailable")
        self.assertIsInstance(raised.exception.exceptions[1].__cause__, OSError)
        self.assertFalse(self.output_directory.exists())

    def test_storage_arguments_are_required(self):
        for bucket, prefix in (("", "run"), ("bucket", "///"), ("bucket", "  ")):
            with self.subTest(bucket=bucket, prefix=prefix):
                with self.assertRaises(ValueError):
                    run_s3_document_screening("ref", "https://example.test", "model.yaml", bucket=bucket, prefix=prefix)
        self.client_factory.assert_not_called()
        self.lpa.search.assert_not_called()

    def test_no_import_of_prioritized_implementation(self):
        path = Path(__file__).resolve().parents[1] / "backend/src/document_screening/implementations/s3_document_agent.py"
        imports = [node for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                   if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertFalse(any("prioritized_document_agent" in ast.unparse(node) for node in imports))


if __name__ == "__main__":
    unittest.main()