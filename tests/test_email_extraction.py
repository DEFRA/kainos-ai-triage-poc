import io
import json
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError
from backend.src.case_intake.tools.email_extractor import extract_from_consultation
from backend.src.case_intake.tools.email_parser import parse_email, parse_eml_attachments, parse_eml_bytes


class EmailParsingTests(unittest.TestCase):
    def test_raw_email_upload_is_opt_in(self):
        # Parse EML and MSG files with and without raw-email uploads enabled.
        # Check that raw storage is present only when explicitly requested.
        parser_module = "backend.src.case_intake.tools.email_parser"
        for extension in ("eml", "msg"):
            for upload_raw in (False, True):
                with self.subTest(extension=extension, upload_raw=upload_raw):
                    payload = {
                        "metadata": {"parse_errors": []},
                        "file": {"filename": f"message.{extension}"},
                        "attachments": [],
                    }
                    storage = {"s3_uri": f"s3://bucket/message.{extension}"}
                    with patch(f"{parser_module}.parse_email_file", return_value=(payload, b"raw email", [])), \
                         patch(f"{parser_module}.redact_emails", side_effect=lambda value: value), \
                         patch(f"{parser_module}.resolve_email_prefix", return_value="test/123/message"), \
                         patch(f"{parser_module}.upload_raw_email_to_s3", return_value=storage) as raw_upload, \
                         patch(f"{parser_module}.upload_attachments_to_s3") as attachment_upload, \
                         patch(f"{parser_module}.upload_json_to_s3") as metadata_upload:
                        if upload_raw:
                            result = parse_email(f"message.{extension}", MagicMock(), "bucket", upload_raw_email=True)
                        else:
                            result = parse_email(f"message.{extension}", MagicMock(), "bucket")

                    self.assertEqual(result["status"], "completed")
                    attachment_upload.assert_called_once()
                    metadata_upload.assert_called_once()
                    if upload_raw:
                        raw_upload.assert_called_once()
                        self.assertEqual(result["json"]["storage"]["raw_email"], storage)
                    else:
                        raw_upload.assert_not_called()
                        self.assertNotIn("raw_email", result["json"]["storage"])

    def test_selected_html_body_is_not_an_attachment(self):
        # Parse an HTML body with a content ID alongside three real attachments.
        # Check that only the HTML file, inline image and attached email are kept.
        message = EmailMessage()
        message.set_content(" ")
        message.add_alternative("<p>Body</p>", subtype="html")
        message.get_payload()[1]["Content-ID"] = "<body>"
        message.add_attachment("<p>Document</p>", subtype="html", filename="document.html")
        message.add_attachment(
            b"image", maintype="image", subtype="png", cid="<image>", disposition="inline"
        )
        attached_email = EmailMessage()
        attached_email.set_content("Attached email")
        message.add_attachment(attached_email, filename="qc.eml")

        attachments, _ = parse_eml_attachments(parse_eml_bytes(message.as_bytes()))

        self.assertEqual(
            [(attachment["filename"], attachment["mime_type"]) for attachment in attachments],
            [("document.html", "text/html"), ("attachment_1", "image/png"), ("qc.eml", "message/rfc822")],
        )

    def test_selected_inline_html_with_filename_remains_an_attachment(self):
        # Parse a named inline HTML file that may also be selected as a body.
        # Check that its filename identifies it as an attachment.
        message = EmailMessage()
        message.set_content("Plain body")
        message.add_attachment("<p>Document</p>", subtype="html", filename="document.html", disposition="inline")

        attachments, _ = parse_eml_attachments(parse_eml_bytes(message.as_bytes()))

        self.assertEqual([attachment["filename"] for attachment in attachments], ["document.html"])


class EmailExtractionTests(unittest.TestCase):
    def extract(self, body, attachments, upload_error=None, attachment_keys=None, model=None, idox_mapping_path=None, model_response="{}", **extract_options):
        # Run the extractor using fake email metadata, S3 and model responses.
        # Return the result, model input and attempted upload for assertions.
        metadata_key = "test/123/email/email_metadata.json"
        metadata = {"body": body, "attachments": attachments}
        s3 = MagicMock()
        s3.get_paginator.return_value.paginate.return_value = [{"Contents": [
            {"Key": metadata_key},
        ] + ([{"Key": "test/123/email/attachments/qc.eml"}]
             if any(attachment.get("filename") == "qc.eml" for attachment in attachments) else [])
          + [{"Key": key} for key in (attachment_keys or [])]}]
        s3.get_object.return_value = {"Body": io.BytesIO(json.dumps(metadata).encode())}
        s3.put_object.side_effect = upload_error
        model = model or MagicMock()
        model.generate.return_value = model_response
        prompt = Path(__file__).resolve().parents[1] / "backend/src/case_intake/prompts/case_intake_extraction.md"

        with patch("backend.src.case_intake.tools.email_extractor.LLMClient") as llm_client:
            llm_client.return_value.__enter__.return_value = model
            result = extract_from_consultation(
                s3_client=s3, bucket="bucket", folder_prefix="test", consultation_id="123",
                model_config_path="model.yaml", prompt_path=prompt, idox_mapping_path=idox_mapping_path,
                **extract_options,
            )

        model_input = json.loads(model.generate.call_args.args[0].split("\n\n", 1)[1])
        saved = json.loads(s3.put_object.call_args.kwargs["Body"])
        return result, model_input, saved

    def test_lpa_website_is_post_processed_before_upload(self):
        # Extract a website using a matching local authority mapping.
        # Check that the corrected website and original extraction are saved.
        with tempfile.TemporaryDirectory() as directory:
            mapping = Path(directory) / "mapping.csv"
            mapping.write_text(
                "lpa_name,assigned_current_lpa_name,lpa_website\n"
                "Example,Example Council,https://planning.example.gov.uk/online-applications/\n",
                encoding="utf-8",
            )
            response = json.dumps({
                "lpa_name": "Example Council",
                "lpa_website": {"value": "https://example.gov.uk", "status": "stated"},
            })
            result, _, saved = self.extract(
                {"text": "Email"}, [], idox_mapping_path=mapping, model_response=response,
            )

        website = saved["records"][0]["extractions"]["lpa_website"]
        self.assertEqual(website["value"], "https://planning.example.gov.uk/online-applications/")
        self.assertEqual(website["original_extraction"]["value"], "https://example.gov.uk")
        self.assertEqual(result["json"], saved)

    def test_attachment_filename_skip_options(self):
        # Exercise each filename-based skip option against original and stored names.
        # Check defaults, disabled options and unavailable attachment handling.
        prefix = "test/123/email/attachments/"
        page = SimpleNamespace(page_number=1, text="Document", image_path=Path("page.png"))
        for flag, keyword, reason in [
            ("skip_checklists", "CheCkLiSt", "Checklist"),
            ("skip_responses", "ReSpOnSe", "Response"),
            ("skip_annexes", "AnNeX", "Annex"),
        ]:
            for match_original_name in (True, False):
                with self.subTest(flag=flag, match_original_name=match_original_name):
                    filename = f"{keyword}.pdf" if match_original_name else "document.pdf"
                    stored_name = "document.pdf" if match_original_name else f"{keyword}.pdf"
                    key = prefix + stored_name
                    attachment = {"filename": filename, "s3_uri": f"s3://bucket/{key}"}
                    result, model_input, saved = self.extract({"text": "Email"}, [attachment], attachment_keys=[key])
                    self.assertEqual(model_input["attachments"], [])
                    self.assertEqual(saved["records"][0]["skipped_attachments"], [{"key": key, "reason": reason}])
                    self.assertEqual(result["warnings"], [])

                    with patch("backend.src.case_intake.tools.email_extractor.parse_pdf", return_value=[page]):
                        _, model_input, saved = self.extract(
                            {"text": "Email"}, [attachment], attachment_keys=[key], **{flag: False},
                        )
                    self.assertEqual(model_input["attachments"][0]["s3_key"], key)
                    self.assertEqual(saved["records"][0]["skipped_attachments"], [])

            with self.subTest(flag=flag, unavailable=True):
                attachment = {"filename": f"{keyword}.pdf"}
                self.extract({"text": "Email"}, [attachment])
                with self.assertRaisesRegex(ValueError, "Expected attachment is unavailable"):
                    self.extract({"text": "Email"}, [attachment], **{flag: False})

    def test_missing_mapping_preserves_website_and_warns(self):
        # Extract a website when the authority mapping file is missing.
        # Check that the original website and warning are returned and saved.
        response = json.dumps({"lpa_website": "https://example.gov.uk"})
        with tempfile.TemporaryDirectory() as directory:
            result, _, saved = self.extract(
                {"text": "Email"}, [], idox_mapping_path=Path(directory) / "missing.csv", model_response=response,
            )

        self.assertEqual(saved["records"][0]["extractions"]["lpa_website"], "https://example.gov.uk")
        self.assertEqual(result["json"], saved)
        self.assertIn("IDOX post-processing failed", result["warnings"][0])
        self.assertEqual(saved["warnings"], result["warnings"])

    def test_unmatched_website_is_preserved_and_warns(self):
        # Extract a website with no match in the authority mapping.
        # Check that the original website and warning are returned and saved.
        response = json.dumps({"lpa_website": "https://unmatched.example.org"})
        with tempfile.TemporaryDirectory() as directory:
            mapping = Path(directory) / "mapping.csv"
            mapping.write_text(
                "lpa_name,assigned_current_lpa_name,lpa_website\n"
                "Example,Example Council,https://planning.example.gov.uk/online-applications/\n",
                encoding="utf-8",
            )
            result, _, saved = self.extract(
                {"text": "Email"}, [], idox_mapping_path=mapping, model_response=response,
            )

        self.assertEqual(saved["records"][0]["extractions"]["lpa_website"], "https://unmatched.example.org")
        self.assertIn("no unique website match", result["warnings"][0])
        self.assertEqual(saved["warnings"], result["warnings"])

    def test_post_processing_error_discards_partial_changes(self):
        # Simulate post-processing failing after mutating the extraction.
        # Check that the original website is saved and the error is reported.
        def fail_after_mutation(result, mapping, mapping_source):
            result["records"][0]["extractions"]["lpa_website"] = "https://changed.example.org"
            raise ValueError("unexpected mapping error")

        response = json.dumps({"lpa_website": "https://example.gov.uk"})
        with patch("backend.src.case_intake.tools.email_extractor.load_idox_mapping", return_value=[{}]), \
             patch("backend.src.case_intake.tools.email_extractor.deterministic_post_processing_lpa_website", side_effect=fail_after_mutation):
            result, _, saved = self.extract(
                {"text": "Email"}, [], idox_mapping_path=Path("mapping.csv"), model_response=response,
            )

        self.assertEqual(saved["records"][0]["extractions"]["lpa_website"], "https://example.gov.uk")
        self.assertEqual(result["json"], saved)
        self.assertIn("unexpected mapping error", result["warnings"][0])

    def test_doc_and_rtf_attachments_are_converted_and_sent(self):
        # Convert DOC and RTF attachments into rendered PDF pages.
        # Check that the model receives the page data and images.
        for extension in ("doc", "rtf"):
            with self.subTest(extension=extension):
                key = f"test/123/email/attachments/application.{extension}"
                attachment = {"filename": f"application.{extension}", "s3_uri": f"s3://bucket/{key}"}
                page = SimpleNamespace(page_number=1, text="Planning details", image_path=Path("page.png"))
                model = MagicMock()
                with patch("backend.src.case_intake.tools.email_extractor.convert_document_to_pdf_libreoffice", return_value=Path("converted.pdf")) as convert, \
                     patch("backend.src.case_intake.tools.email_extractor.parse_pdf", return_value=[page]) as parse:
                    result, model_input, saved = self.extract({"text": "Email"}, [attachment], attachment_keys=[key], model=model)

                self.assertEqual(convert.call_args.args[0].suffix, f".{extension}")
                self.assertEqual(parse.call_args.args[0], Path("converted.pdf"))
                self.assertEqual(model.generate.call_args.kwargs["attachments"], [page.image_path])
                self.assertEqual(model_input["attachments"], [{
                    "filename": f"application.{extension}", "s3_key": key, "type": extension,
                    "pages": [{"page_number": 1, "text": "Planning details", "image_number": 1}],
                }])
                self.assertEqual(saved["records"][0]["processed_attachments"], [key])
                self.assertEqual(result["status"], "completed")

    def test_doc_and_rtf_missing_attachments_raise(self):
        # Extract emails referencing unavailable DOC and RTF attachments.
        # Check that missing supported documents stop the extraction.
        for extension in ("doc", "rtf"):
            with self.subTest(extension=extension):
                attachment = {"filename": f"missing.{extension}"}
                with self.assertRaisesRegex(ValueError, "Expected attachment is unavailable"):
                    self.extract({"text": "Email"}, [attachment])

    def test_word_and_rtf_conversion_failure_uses_text_fallback(self):
        # Simulate conversion failure for each supported Word and RTF format.
        # Check the text-only model input and saved conversion warnings.
        for extension in ("doc", "docx", "rtf"):
            with self.subTest(extension=extension):
                key = f"test/123/email/attachments/application.{extension}"
                attachment = {"filename": f"application.{extension}", "s3_uri": f"s3://bucket/{key}"}
                model = MagicMock()
                with patch("backend.src.case_intake.tools.email_extractor.convert_document_to_pdf_libreoffice", side_effect=FileNotFoundError("LibreOffice unavailable")), \
                     patch(f"backend.src.case_intake.tools.email_extractor.parse_{extension}", return_value="Fallback text") as parse:
                    result, model_input, saved = self.extract({"text": "Email"}, [attachment], attachment_keys=[key], model=model)
                parse.assert_called_once()
                self.assertEqual(model_input["attachments"][0]["text"], "Fallback text")
                self.assertEqual(model_input["attachments"][0]["type"], extension)
                self.assertEqual(model.generate.call_args.kwargs["attachments"], [])
                self.assertIn("test/123/email/email_metadata.json", result["warnings"][0])
                self.assertIn(key, result["warnings"][0])
                self.assertEqual(saved["records"][0]["warnings"], result["warnings"])
                self.assertEqual(saved["warnings"], result["warnings"])

    def test_unsupported_attachments_are_reported_without_duplicate_email_warning(self):
        # Process an attached email, an unsupported image and a skipped checklist.
        # Check that each relevant warning is reported once and saved.
        prefix = "test/123/email/attachments/"
        keys = [prefix + "message.dat", prefix + "diagram.png", prefix + "checklist.xlsx"]
        attachments = [
            {"filename": "message.dat", "mime_type": "message/rfc822", "s3_uri": f"s3://bucket/{keys[0]}"},
            {"filename": "diagram.png", "is_inline": True, "s3_uri": f"s3://bucket/{keys[1]}"},
            {"filename": "checklist.xlsx", "s3_uri": f"s3://bucket/{keys[2]}"},
        ]
        result, _, saved = self.extract({"text": "Email"}, attachments, attachment_keys=keys)

        self.assertEqual(len(result["warnings"]), 2)
        self.assertIn("attached email content was not processed", result["warnings"][0])
        self.assertIn(keys[1], result["warnings"][1])
        self.assertNotIn(keys[0], result["warnings"][1])
        self.assertNotIn(keys[2], result["warnings"][1])
        self.assertEqual(saved["records"][0]["warnings"], result["warnings"])
        self.assertEqual(saved["warnings"], result["warnings"])

    def test_link_only_body_with_attached_email_requires_review(self):
        # Extract a link-only HTML body with an attached email.
        # Check the readable body, review warning and skipped attachment.
        result, model_input, saved = self.extract(
            {"text": "\r\n", "html": '<a href="cid:qc">QC email</a>'},
            [{"filename": "qc.eml", "mime_type": "message/rfc822"}],
        )

        self.assertEqual(model_input["email"]["body"], {"text": "QC email"})
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("Review required", result["warnings"][0])
        self.assertEqual(saved["records"][0]["warnings"], result["warnings"])
        self.assertEqual(saved["records"][0]["skipped_attachments"][0]["reason"], "Unsupported file type")

    def test_nonempty_plain_text_is_preserved_and_attached_email_is_flagged(self):
        # Extract an email with plain text, HTML and an attached email.
        # Check that plain text is retained and the attachment triggers a warning.
        result, model_input, saved = self.extract(
            {"text": "Useful plain text", "html": '<a href="cid:qc">QC email</a>'},
            [{"filename": "qc.eml", "mime_type": "message/rfc822"}],
        )

        self.assertEqual(model_input["email"]["body"], {"text": "Useful plain text"})
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("attached email content was not processed", result["warnings"][0])
        self.assertEqual(saved["records"][0]["warnings"], result["warnings"])

    def test_upload_conflict_is_skipped(self):
        # Simulate another process creating the output before it can be saved.
        # Check that the result is skipped and has no saved S3 key.
        error = ClientError(
            {"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412}},
            "PutObject",
        )
        result, _, _ = self.extract({"text": "Email body"}, [], upload_error=error)

        self.assertEqual(result["status"], "skipped")
        self.assertIsNone(result["s3_key"])

    def test_html_fallback_keeps_web_links_and_omits_scripts(self):
        # Extract an HTML body containing a web link and a script.
        # Check that the link is retained, the script omitted and no warning raised.
        result, model_input, _ = self.extract(
            {"text": "", "html": '<p>Visit <a href="https://example.org">the portal</a></p><script>hidden</script>'},
            [],
        )

        self.assertIn("https://example.org", model_input["email"]["body"]["text"])
        self.assertNotIn("hidden", model_input["email"]["body"]["text"])
        self.assertEqual(result["warnings"], [])


if __name__ == "__main__":
    unittest.main()