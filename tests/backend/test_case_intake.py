import unittest
from unittest.mock import patch
from backend.src.case_intake.implementations.case_intake_orchestrator import execute_case_intake_stage


class CaseIntakeTests(unittest.TestCase):
    def setUp(self):
        self.arguments = {
            "email_file_path": "consultation.eml",
            "bucket": "bucket",
            "folder_prefix": "emails",
            "consultation_id": "123",
            "model_config_path": "model.yaml",
            "prompt_path": "prompt.md",
        }

    @patch("backend.src.case_intake.implementations.case_intake_orchestrator.extract_from_consultation")
    @patch("backend.src.case_intake.implementations.case_intake_orchestrator.parse_email")
    @patch("backend.src.case_intake.implementations.case_intake_orchestrator.create_s3_client")
    def test_extracts_case_intake_from_parsed_email(self, create_client, parse_email, extract):
        parse_email.return_value = {
            "status": "completed",
            "warnings": ["Parser warning"],
            "s3_key": "metadata.json",
            "json": {"metadata": {"parse_status": "ok"}, "storage": {"prefix": "emails/123/email"}},
        }
        extraction = {"lpa_name": "Example Council"}
        extract.return_value = {
            "status": "completed",
            "warnings": ["Extractor warning"],
            "json": {"records": [{"extractions": extraction}]},
        }

        result = execute_case_intake_stage(**self.arguments)

        self.assertEqual(result, {
            "status": "completed",
            "warnings": ["Parser warning", "Extractor warning"],
            "email_extraction": [extraction],
        })
        self.assertEqual(parse_email.call_args.kwargs["s3_client"], create_client.return_value)
        self.assertEqual(extract.call_args.kwargs["folder_prefix"], "emails")
        self.assertEqual(extract.call_args.kwargs["consultation_id"], "123")

    @patch("backend.src.case_intake.implementations.case_intake_orchestrator.extract_from_consultation")
    @patch("backend.src.case_intake.implementations.case_intake_orchestrator.parse_email")
    @patch("backend.src.case_intake.implementations.case_intake_orchestrator.create_s3_client")
    def test_skipped_email_does_not_run_extraction(self, create_client, parse_email, extract):
        parse_email.return_value = {"status": "skipped", "warnings": ["Email already exists"]}

        result = execute_case_intake_stage(**self.arguments)

        self.assertEqual(result, {"status": "skipped", "warnings": ["Email already exists"]})
        extract.assert_not_called()


if __name__ == "__main__":
    unittest.main()