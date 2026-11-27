import ast
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
from backend.src.utilities.llm_client import _usage_collector, collect_llm_usage
from document_screening_fixtures import valid_document_findings


MODULE = "backend.src.document_screening.implementations.s3_document_agent"


class S3DocumentAgentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.default_implementation_config = self.root / "implementation.yaml"
        self.default_implementation_config.write_text(
            "document_screening:\n  max_concurrent_downloads: 5\n"
            "  include_attachments: true\nlpa: {}\n",
            encoding="utf-8",
        )
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
        self.llm.generate.side_effect = self.generate
        self.objects = {}
        self.s3 = Mock()
        self.s3.put_object.side_effect = self.put_object
        self.output_directory = None
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        contexts.enter_context(patch(f"{MODULE}.PublicAccessClient", return_value=self.lpa))
        self.llm_factory = contexts.enter_context(patch(f"{MODULE}.LLMClient"))
        self.llm_factory.return_value.__enter__.return_value = self.llm
        contexts.enter_context(patch(
            f"{MODULE}.ModelConfig.from_yaml", side_effect=lambda path: self.llm.config,
        ))
        self.parser = contexts.enter_context(patch(f"{MODULE}.parse_pdf", side_effect=self.parse_pdf))
        self.client_factory = contexts.enter_context(patch(f"{MODULE}.create_s3_client", return_value=self.s3))

    def numbered_ranking(self, records):
        """Return a ranking response using the numbers sent to the model, as a real model would."""
        numbers = {
            str(document["id"]): number
            for number, document in enumerate(self.metadata["documents"], start=1)
        }
        return json.dumps({"documents": [
            {**record, "id": numbers[record["id"]]} for record in records
        ]})

    def generate(self, prompt, **kwargs):
        if prompt.startswith("Prioritize"):
            return self.numbered_ranking(self.ranking)
        if prompt.startswith("Pre-screening batch"):
            return f"Unvalidated output for {prompt.split('.', 1)[0]}"
        return json.dumps(self.findings)

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

    def parse_pdf(
        self, document_path, directory, *, dpi, image_format="png", jpeg_quality=90,
        render_images=True,
    ):
        if not render_images:
            return [ParsedPage(document_path, 1, "Page text", None, True)]
        directory.mkdir(exist_ok=True)
        suffix = ".jpg" if image_format == "jpeg" else ".png"
        image_path = directory / f"{document_path.stem}-page-0001{suffix}"
        image_path.write_bytes(b"page-image")
        return [ParsedPage(document_path, 1, "Page text", image_path, True)]

    def put_object(self, *, Bucket, Key, Body):
        self.objects[Key] = Body
        return {"VersionId": "version-1"}

    def run_agent(self, **kwargs):
        kwargs.setdefault("implementation_config_path", self.default_implementation_config)
        return run_s3_document_screening(
            "25/123", "https://example.test/", "model.yaml",
            bucket="bucket", prefix="/case/run/", **kwargs,
        )

    def prompt_source_data(self, prompt):
        content = prompt.split(
            "Application metadata and downloaded document evidence:\n", 1
        )[1]
        for marker in (
            "\n\nAttachments follow in the numbered order recorded above.",
            "\n\nNo file attachments are included.",
        ):
            if marker in content:
                content = content.split(marker, 1)[0]
                break
        return json.loads(content)

    def test_complete_run_uploads_all_artifacts_and_preserves_output(self):
        cutoff = date(2026, 10, 21)
        result = self.run_agent(s3_client=self.s3, ignore_after=cutoff, dpi=200)
        self.client_factory.assert_not_called()
        self.lpa.get_metadata.assert_called_once_with("KEY", self.output_directory, ignore_after=cutoff)
        self.lpa.download_documents_concurrently.assert_called_once_with(
            "KEY", self.metadata, self.output_directory, ["11"], max_workers=5,
        )
        self.parser.assert_called_once_with(
            self.output_directory / "11.pdf",
            self.output_directory / "rendered-pages",
            dpi=200,
            image_format="jpeg",
            jpeg_quality=90,
            render_images=True,
        )
        expected = {
            "11.pdf", "rendered-pages/11-page-0001.jpg", "KEY-metadata.json",
            "KEY-prioritization-response.txt", "pre-screening/batch-01-response.txt",
            "KEY-prioritized-document-findings.json",
            "KEY-prioritized-final-output.json", "KEY-prioritized-document-screening.json",
        }
        self.assertEqual(set(result["storage"]["objects"]), expected)
        self.assertEqual(self.objects["case/run/11.pdf"], b"pdf-11")
        self.assertEqual(self.objects["case/run/rendered-pages/11-page-0001.jpg"], b"page-image")
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
        self.assertEqual(result["sources"][0]["saved_as"], "11.pdf")
        self.assertEqual(result["sources"][0]["s3"], {"bucket": "bucket", "key": "case/run/11.pdf"})
        self.assertEqual(stored_result["sources"][0]["s3"], result["sources"][0]["s3"])
        self.assertEqual(result["storage"]["objects"]["11.pdf"]["version"], "version-1")
        self.assertEqual(self.s3.put_object.call_args.kwargs["Key"], "case/run/KEY-prioritized-document-screening.json")
        self.assertNotIn("https://example.test/11.pdf", self.llm.generate.call_args.args[0])
        self.assertFalse(self.output_directory.exists())

    def test_only_pages_with_visual_content_are_attached(self):
        def parse_text_and_visual_pages(
            document_path, directory, *, dpi, image_format, jpeg_quality, render_images=True,
        ):
            directory.mkdir(exist_ok=True)
            text_image = directory / "text-only.jpg"
            visual_image = directory / "visual.jpg"
            text_image.write_bytes(b"text image")
            visual_image.write_bytes(b"visual image")
            return [
                ParsedPage(document_path, 1, "Extracted text", text_image),
                ParsedPage(document_path, 2, "Diagram caption", visual_image, True),
            ]

        self.parser.side_effect = parse_text_and_visual_pages

        result = self.run_agent(s3_client=self.s3)

        self.assertEqual(
            self.llm.generate.call_args_list[1].kwargs["attachments"],
            [self.output_directory / "rendered-pages" / "visual.jpg"],
        )
        pages = result["sources"][0]["pages"]
        self.assertNotIn("attachment_number", pages[0])
        self.assertEqual(pages[1]["attachment_number"], 1)

    def test_text_only_config_sends_no_attachments_or_rendered_pages(self):
        config_path = self.root / "text-only.yaml"
        config_path.write_text(
            "document_screening:\n  include_attachments: false\nlpa: {}\n",
            encoding="utf-8",
        )

        result = self.run_agent(
            s3_client=self.s3, implementation_config_path=config_path,
        )

        self.assertEqual(self.llm.generate.call_args_list[1].kwargs["attachments"], [])
        self.parser.assert_called_once_with(
            self.output_directory / "11.pdf",
            self.output_directory / "rendered-pages",
            dpi=150,
            image_format="jpeg",
            jpeg_quality=90,
            render_images=False,
        )
        self.assertFalse(any(
            "rendered-pages/" in key for key in result["storage"]["objects"]
        ))
        self.assertNotIn("attachment_number", result["sources"][0]["pages"][0])
        self.assertTrue(any(
            "page images were not sent" in warning
            for warning in result["document_findings"]["limitations"]
        ))

    def test_bedrock_usage_is_preserved_in_result_artifact(self):
        self.llm.config.provider = "bedrock"

        def generate(prompt, **kwargs):
            self.llm.last_usage = {
                "input_tokens": 5, "output_tokens": 10, "total_tokens": 15,
            }
            return self.generate(prompt, **kwargs)

        self.llm.generate.side_effect = generate

        result = self.run_agent(s3_client=self.s3)

        expected_usage = {
            "input_tokens": 5, "output_tokens": 10, "total_tokens": 15,
        }
        self.assertEqual(result["usage"], {
            "prioritization": expected_usage,
            "pre_screening": [expected_usage],
            "screening": expected_usage,
        })
        stored_result = json.loads(
            self.objects["case/run/KEY-prioritized-document-screening.json"]
        )
        self.assertEqual(stored_result["usage"], result["usage"])

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
        source_data = self.prompt_source_data(prompt)
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
        self.assertEqual(
            [(source["saved_as"], source["s3"]["key"]) for source in result["sources"]],
            [("11.docx", "case/run/11.docx"), ("12.xlsx", "case/run/12.xlsx")],
        )
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
        source_data = self.prompt_source_data(prompt)
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
        source_data = self.prompt_source_data(prompt)
        source = source_data["sources"][0]
        self.assertIn("unsupported file format .doc", source["extraction_error"])
        self.assertEqual(self.llm.generate.call_args_list[1].kwargs["attachments"], [])
        self.assertTrue(any("unsupported file format .doc" in item for item in result["document_findings"]["limitations"]))

    def test_include_maybe_preserves_uncertain_skip(self):
        config_path = self.root / "config.yaml"
        config_path.write_text("document_screening:\n  include_maybe: true\nlpa: {}\n", encoding="utf-8")
        self.ranking[2]["uncertain"] = True
        result = self.run_agent(implementation_config_path=config_path)
        self.client_factory.assert_called_once_with()
        self.assertEqual(self.lpa.download_documents_concurrently.call_args.args[3], ["11", "12"])
        self.assertEqual(result["prioritization"][2]["priority"], "skip")
        self.assertTrue(result["prioritization"][2]["uncertain"])

    def test_missing_prioritization_is_logged_and_defaults_to_maybe(self):
        self.llm.generate.side_effect = [
            self.numbered_ranking(self.ranking[:2]),
            "Unvalidated batch output",
            json.dumps(self.findings),
        ]

        result = self.run_agent(s3_client=self.s3)

        self.assertEqual(
            self.lpa.download_documents_concurrently.call_args.args[3], ["11"],
        )
        missing_document = result["metadata"]["documents"][2]
        self.assertEqual(missing_document["priority"], "maybe")
        self.assertTrue(missing_document["priority_uncertain"])
        self.assertTrue(missing_document["priority_missing_from_response"])
        self.assertEqual(
            result["metadata"]["prioritization_warnings"][0]["document_ids"],
            ["13"],
        )
        stored_metadata = json.loads(self.objects["case/run/KEY-metadata.json"])
        self.assertEqual(
            stored_metadata["prioritization_warnings"],
            result["metadata"]["prioritization_warnings"],
        )

    def test_download_failure_is_recorded_as_limitation(self):
        def fail_download(application_key, metadata, directory, document_ids, *, max_workers):
            metadata["documents"][0]["download_error"] = "download failed"
            (directory / "KEY-metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

        self.lpa.download_documents_concurrently.side_effect = fail_download
        result = self.run_agent(s3_client=self.s3)
        self.assertIn("Document 11 was not downloaded: download failed", result["document_findings"]["limitations"])
        self.assertEqual(result["sources"], [])

    def test_documents_are_pre_screened_in_batches_then_consolidated(self):
        config_path = self.root / "batched.yaml"
        config_path.write_text(
            "document_screening:\n  include_attachments: true\n"
            "  pre_screening_batch_size: 2\n  max_concurrent_pre_screening_calls: 2\nlpa: {}\n",
            encoding="utf-8",
        )
        self.metadata["documents"] += [
            {"id": str(document_id), "published": "20 Oct 2026", "type": "Plan",
             "drawing_number": None, "description": "Plan",
             "url": f"https://example.test/{document_id}.pdf"}
            for document_id in (14, 15)
        ]
        self.ranking = [
            {"id": document["id"], "priority": "must", "uncertain": False, "reason": "Relevant"}
            for document in self.metadata["documents"]
        ]
        usage_seen_by_batches = []

        def generate(prompt, **kwargs):
            if prompt.startswith("Pre-screening batch"):
                usage_seen_by_batches.append(_usage_collector.get())
            return self.generate(prompt, **kwargs)

        self.llm.generate.side_effect = generate

        with collect_llm_usage() as usage:
            result = self.run_agent(s3_client=self.s3, implementation_config_path=config_path)

        self.assertEqual(usage_seen_by_batches, [usage] * 3)
        calls = self.llm.generate.call_args_list
        batch_calls = sorted(
            (call for call in calls if call.args[0].startswith("Pre-screening batch")),
            key=lambda call: call.args[0],
        )
        self.assertEqual(
            [[source["id"] for source in self.prompt_source_data(call.args[0])["sources"]]
             for call in batch_calls],
            [["11", "12"], ["13", "14"], ["15"]],
        )
        for call in batch_calls:
            attachments = call.kwargs["attachments"]
            sources = self.prompt_source_data(call.args[0])["sources"]
            self.assertEqual(
                [source["pages"][0]["attachment_number"] for source in sources],
                list(range(1, len(attachments) + 1)),
            )
            self.assertEqual(
                attachments,
                [self.output_directory / "rendered-pages" / f"{source['id']}-page-0001.jpg"
                 for source in sources],
            )

        screening_call = calls[-1]
        self.assertNotIn("attachments", screening_call.kwargs)
        self.assertIn("## Consolidation rules", screening_call.kwargs["system_prompt"])
        self.assertIn("## Output", screening_call.kwargs["system_prompt"])
        for batch_number in (1, 2, 3):
            self.assertIn(
                f"Unvalidated output for Pre-screening batch {batch_number} of 3",
                screening_call.args[0],
            )
        self.assertEqual(
            [(batch["batch_number"], batch["document_ids"]) for batch in result["pre_screening"]],
            [(1, ["11", "12"]), (2, ["13", "14"]), (3, ["15"])],
        )
        self.assertEqual([source["pre_screening_batch"] for source in result["sources"]], [1, 1, 2, 2, 3])
        self.assertEqual(
            self.objects["case/run/pre-screening/batch-03-response.txt"],
            b"Unvalidated output for Pre-screening batch 3 of 3",
        )
        self.assertNotIn("pre_screening", result["final_output"])

    def test_model_configs_can_be_one_path_or_one_path_per_call(self):
        model_configs = {
            "prioritization": "ranking.yaml",
            "pre_screening": "batches.yaml",
            "screening": "consolidation.yaml",
        }
        result = run_s3_document_screening(
            "25/123", "https://example.test/", model_configs,
            bucket="bucket", prefix="run", s3_client=self.s3,
            implementation_config_path=self.default_implementation_config,
        )
        self.assertEqual(
            [call.args[0] for call in self.llm_factory.call_args_list],
            [Path("ranking.yaml"), Path("batches.yaml"), Path("consolidation.yaml")],
        )
        self.assertEqual(
            {call: model["config_path"] for call, model in result["models"].items()},
            model_configs,
        )

        self.llm_factory.reset_mock()
        self.objects.clear()
        self.run_agent(s3_client=self.s3)
        self.assertEqual(
            [call.args[0] for call in self.llm_factory.call_args_list], [Path("model.yaml")] * 3,
        )

    def test_invalid_model_configs_stop_before_any_work(self):
        for model_configs in ({"prioritization": "a.yaml", "screening": "b.yaml"}, "", 123):
            with self.subTest(model_configs=model_configs):
                with self.assertRaisesRegex(ValueError, "model_configs|non-empty path"):
                    run_s3_document_screening(
                        "25/123", "https://example.test/", model_configs,
                        bucket="bucket", prefix="run", s3_client=self.s3,
                    )
        self.lpa.search.assert_not_called()

    def test_failed_pre_screening_batch_stops_screening_and_uploads_completed_batches(self):
        config_path = self.root / "batched.yaml"
        config_path.write_text(
            "document_screening:\n  pre_screening_batch_size: 1\n"
            "  max_concurrent_pre_screening_calls: 1\nlpa: {}\n",
            encoding="utf-8",
        )
        self.ranking[1]["priority"] = "must"

        def generate(prompt, **kwargs):
            if prompt.startswith("Pre-screening batch 2"):
                raise RuntimeError("context length exceeded")
            return self.generate(prompt, **kwargs)

        self.llm.generate.side_effect = generate

        with self.assertRaisesRegex(RuntimeError, "context length exceeded"):
            self.run_agent(s3_client=self.s3, implementation_config_path=config_path)

        self.assertIn("case/run/pre-screening/batch-01-response.txt", self.objects)
        self.assertFalse(any(
            "Pre-screening batch" not in call.args[0] and not call.args[0].startswith("Prioritize")
            for call in self.llm.generate.call_args_list
        ))

    def test_ranking_failure_uploads_metadata_and_stops_before_download(self):
        self.llm.generate.side_effect = RuntimeError("ranking failed")
        with self.assertRaisesRegex(RuntimeError, "ranking failed"):
            self.run_agent(s3_client=self.s3)
        self.assertEqual(set(self.objects), {"case/run/KEY-metadata.json"})
        self.lpa.download_documents_concurrently.assert_not_called()

    def test_large_documents_get_their_own_batch_and_text_is_cleaned(self):
        config_path = self.root / "sized.yaml"
        config_path.write_text(
            "document_screening:\n  pre_screening_batch_size: 3\n"
            "  max_pre_screening_batch_characters: 2000\nlpa: {}\n",
            encoding="utf-8",
        )
        for document in self.ranking:
            document["priority"] = "must"

        def parse_sized_pdf(document_path, directory, **kwargs):
            text = ("Long   report text ----- " * 200) if document_path.stem == "12" else "Short   text"
            return [ParsedPage(document_path, 1, text, None, False)]

        self.parser.side_effect = parse_sized_pdf

        result = self.run_agent(s3_client=self.s3, implementation_config_path=config_path)

        self.assertEqual(
            [batch["document_ids"] for batch in result["pre_screening"]], [["11"], ["12"], ["13"]],
        )
        self.assertGreater(result["pre_screening"][1]["document_characters"], 2000)
        self.assertEqual(result["sources"][0]["pages"][0]["text"], "Short text")
        self.assertNotIn("-----", result["sources"][1]["pages"][0]["text"])

    def test_malformed_prioritization_response_is_uploaded(self):
        self.ranking[0]["priority"] = "essential"
        with self.assertRaisesRegex(ValueError, "Invalid priority"):
            self.run_agent(s3_client=self.s3)
        self.assertIn("case/run/KEY-prioritization-response.txt", self.objects)
        self.lpa.download_documents_concurrently.assert_not_called()

    def test_ranking_id_problems_are_warnings_with_real_ids(self):
        self.llm.generate.side_effect = lambda prompt, **kwargs: (
            json.dumps({"documents": [
                {"id": 1, "priority": "maybe", "uncertain": False, "reason": "First"},
                {"id": "1", "priority": "must", "uncertain": False, "reason": "Repeated"},
                {"id": 2, "priority": "skip", "uncertain": False, "reason": "Report"},
                {"id": 99, "priority": "skip", "uncertain": False, "reason": "Invented"},
            ]}) if prompt.startswith("Prioritize") else self.generate(prompt, **kwargs)
        )

        result = self.run_agent(s3_client=self.s3)

        warnings = {
            warning["type"]: warning for warning in result["metadata"]["prioritization_warnings"]
        }
        self.assertEqual(warnings["duplicate_document_prioritizations"]["document_ids"], ["11"])
        self.assertEqual(warnings["missing_document_prioritizations"]["document_ids"], ["13"])
        self.assertEqual(warnings["unexpected_document_ids"]["returned_ids"], ["99"])
        first = result["prioritization"][0]
        self.assertEqual((first["id"], first["priority"], first["uncertain"]), ("11", "must", True))
        self.assertEqual(self.lpa.download_documents_concurrently.call_args.args[3], ["11"])

    def test_document_rules_bypass_ranking_and_remaining_documents_are_renumbered(self):
        config_path = self.root / "rules.yaml"
        config_path.write_text(
            "document_screening:\n"
            "  must_document_patterns: ['location plans?']\n"
            "  skip_document_type_patterns: ['superseded', 'photo']\n"
            "lpa: {}\n",
            encoding="utf-8",
        )
        self.metadata["documents"][1].update(type="Plans - SUPERSEDED", description="Site Location Plan")
        ranking_prompts = []

        def generate(prompt, **kwargs):
            if prompt.startswith("Prioritize"):
                ranking_prompts.append(prompt)
                return json.dumps({"documents": [
                    {"id": 1, "priority": "must", "uncertain": False, "reason": "Planning statement"},
                ]})
            return self.generate(prompt, **kwargs)

        self.llm.generate.side_effect = generate

        result = self.run_agent(s3_client=self.s3, implementation_config_path=config_path)

        ranked = json.loads(ranking_prompts[0].split("\n", 1)[1])["documents"]
        self.assertEqual([(item["id"], item["description"]) for item in ranked], [(1, "Planning")])
        self.assertEqual(
            [(item["id"], item["priority"], item["source"]) for item in result["prioritization"]],
            [("11", "must", "model"), ("12", "must", "rule"), ("13", "skip", "rule")],
        )
        self.assertIn("location plans?", result["prioritization"][1]["reason"])
        self.assertIn("photo", result["prioritization"][2]["reason"])
        self.assertEqual(self.lpa.download_documents_concurrently.call_args.args[3], ["11", "12"])
        self.assertEqual(result["metadata"]["documents"][2]["priority_source"], "rule")

    def test_all_documents_skipped_by_rules_warns_without_ranking_call(self):
        config_path = self.root / "skip-all.yaml"
        config_path.write_text(
            "document_screening:\n  skip_document_type_patterns: ['statement|report|photo']\nlpa: {}\n",
            encoding="utf-8",
        )

        result = self.run_agent(s3_client=self.s3, implementation_config_path=config_path)

        self.assertFalse(any(
            call.args[0].startswith("Prioritize") for call in self.llm.generate.call_args_list
        ))
        self.assertEqual(
            result["metadata"]["prioritization_warnings"][0]["type"], "all_documents_skipped_by_rules",
        )
        self.assertEqual(self.lpa.download_documents_concurrently.call_args.args[3], [])
        self.assertNotIn("KEY-prioritization-response.txt", result["storage"]["objects"])

    def test_invalid_response_retains_partial_artifacts(self):
        self.llm.generate.side_effect = [
            self.numbered_ranking(self.ranking), "Unvalidated batch output", "not JSON",
        ]
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
        path = Path(__file__).resolve().parents[2] / "backend/src/document_screening/implementations/s3_document_agent.py"
        imports = [node for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                   if isinstance(node, (ast.Import, ast.ImportFrom))]
        self.assertFalse(any("prioritized_document_agent" in ast.unparse(node) for node in imports))


if __name__ == "__main__":
    unittest.main()