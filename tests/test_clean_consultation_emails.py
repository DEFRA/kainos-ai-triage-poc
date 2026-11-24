import csv
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from email import policy
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from backend.src.case_intake.tools.email_parser import parse_eml_bytes
from pre_processing import clean_consultation_emails as cleaner


def make_email(subject: str, body: str, attachments: list[tuple[str, bytes]] | None = None) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = "sender@example.test"
    message.set_content(body)
    for filename, payload in attachments or []:
        message.add_attachment(payload, maintype="application", subtype="pdf", filename=filename)
    return message


def qc_thread(consultation_id: str, root_body: str, reviewer_note: str) -> str:
    return (
        f"QC reviewer comment: {reviewer_note}\n\n"
        "From: reviewer@example.test\n"
        "Sent: Tuesday, 1 January 2026 10:00\n"
        "To: caseworker@example.test\n"
        f"Subject: QC {consultation_id}\n\n"
        "Please complete the check.\n\n"
        "From: planning@example.test\n"
        "Sent: Monday, 31 December 2025 09:00\n"
        "To: consultations@example.test\n"
        f"Subject: Application {consultation_id}\n\n"
        f"{root_body}"
    )


class CleanConsultationEmailTests(unittest.TestCase):
    def test_original_email_body_excludes_qc_thread(self):
        message = make_email("QC 123456", qc_thread("123456", "Original consultation", "internal note"))

        self.assertEqual(cleaner.original_email_body(message), "Original consultation")

    def test_unrecognized_thread_body_is_not_used(self):
        message = make_email("QC 123456", "QC complete. Please file this response.")

        self.assertEqual(cleaner.original_email_body(message), "")

    def test_same_reference_qcs_merge_roots_and_deduplicate_outputs(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_folder = root / "123456"
            input_folder.mkdir()
            output_root = root / "cleaned"

            parents = [
                ("123456 parent one.eml", "Parent message one", [("parent-secret.pdf", b"parent data")]),
                ("123456 parent two.eml", "Parent message two", [("other-parent.pdf", b"other data")]),
            ]
            for name, body, attachments in parents:
                (input_folder / name).write_bytes(
                    make_email("Application 123456", body, attachments).as_bytes(policy=policy.default)
                )

            shared_attachment = b"same plan content"
            qc_messages = [
                (
                    "123456 QC one.eml",
                    qc_thread("123456", "Original consultation A", "do not include A"),
                    [("plan-a.pdf", shared_attachment)],
                ),
                (
                    "123456 filing update.eml",
                    "QC COMPLETE - Please issue.\n\n"
                    + qc_thread("123456", "Original consultation B", "do not include B"),
                    [("plan-b.pdf", shared_attachment)],
                ),
                (
                    "654321 QC unrelated.eml",
                    qc_thread("654321", "Unrelated consultation", "unrelated note"),
                    [("unrelated.pdf", b"other plan")],
                ),
            ]
            for name, body, attachments in qc_messages:
                message = make_email("Filing update", body, attachments)
                if name == "123456 QC one.eml":
                    nested_non_qc = make_email(
                        "Follow-up 123456",
                        "This is a later message nested in a QC email.",
                        [("nested-document.pdf", b"must not be included")],
                    )
                    message.add_attachment(nested_non_qc, filename="follow-up.eml")
                (input_folder / name).write_bytes(message.as_bytes(policy=policy.default))

            with patch.object(cleaner, "OUTPUT_ROOT", output_root):
                rows = cleaner.process_consultation(input_folder)

            self.assertEqual([row["consultation_id"] for row in rows], ["123456", "654321"])
            self.assertEqual([row["status"] for row in rows], ["ok", "ok"])
            output_files = list(output_root.rglob("*.eml"))
            self.assertEqual(len(output_files), 2)

            cleaned = parse_eml_bytes((output_root / "123456" / "123456 clean.eml").read_bytes())
            body = cleaned.get_body(preferencelist=("plain",)).get_content()
            self.assertIn("Original consultation A", body)
            self.assertIn("Original consultation B", body)
            self.assertNotIn("reviewer comment", body)
            self.assertNotIn("Please complete the check", body)
            self.assertNotIn("Parent message", body)
            self.assertNotIn("Unrelated consultation", body)
            attachments = list(cleaned.iter_attachments())
            self.assertEqual(len(attachments), 2)
            self.assertIn("nested-document.pdf", {part.get_filename() for part in attachments})
            self.assertTrue(
                {"plan-a.pdf", "plan-b.pdf"}.intersection(
                    {part.get_filename() for part in attachments}
                )
            )
            self.assertEqual(cleaned.get("Subject"), "Application 123456")

            unrelated = parse_eml_bytes((output_root / "654321" / "654321 clean.eml").read_bytes())
            self.assertIn(
                "Unrelated consultation",
                unrelated.get_body(preferencelist=("plain",)).get_content(),
            )

    def test_no_qc_emails_skips_folder_without_using_parent(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            input_folder = Path(temporary_directory) / "123456"
            input_folder.mkdir()
            (input_folder / "123456 parent.eml").write_bytes(
                make_email(
                    "Application 123456",
                    "Parent message must not be used",
                    [("plan.pdf", b"parent plan")],
                ).as_bytes(policy=policy.default)
            )

            rows = cleaner.process_consultation(input_folder)

        self.assertEqual(rows[0]["status"], "skipped")
        self.assertEqual(rows[0]["note"], "no QC email found")

    def test_main_merges_same_consultation_across_source_folders(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            input_root = root / "input"
            output_root = root / "cleaned"
            first_folder = input_root / "batch-a"
            second_folder = input_root / "batch-b"
            first_folder.mkdir(parents=True)
            second_folder.mkdir()
            shared_attachment = b"same plan content"

            (first_folder / "123456 QC one.eml").write_bytes(
                make_email(
                    "QC 123456",
                    qc_thread("123456", "Original consultation A", "review note A"),
                    [("plan-a.pdf", shared_attachment)],
                ).as_bytes(policy=policy.default)
            )
            (second_folder / "123456 QC two.eml").write_bytes(
                make_email(
                    "QC 123456",
                    qc_thread("123456", "Original consultation B", "review note B"),
                    [("plan-b.pdf", shared_attachment)],
                ).as_bytes(policy=policy.default)
            )

            with (
                patch.object(cleaner, "INPUT_ROOT", input_root),
                patch.object(cleaner, "OUTPUT_ROOT", output_root),
                patch.object(cleaner, "LOG_PATH", output_root / "preprocessing_log.csv"),
                redirect_stdout(io.StringIO()),
            ):
                cleaner.main()

            with (output_root / "preprocessing_log.csv").open(encoding="utf-8", newline="") as file:
                rows = list(csv.DictReader(file))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["consultation_id"], "123456")
            self.assertEqual(rows[0]["status"], "ok")

            output_files = list(output_root.rglob("*.eml"))
            self.assertEqual(len(output_files), 1)
            cleaned = parse_eml_bytes(output_files[0].read_bytes())
            body = cleaned.get_body(preferencelist=("plain",)).get_content()
            self.assertIn("Original consultation A", body)
            self.assertIn("Original consultation B", body)
            self.assertNotIn("review note", body)
            self.assertEqual(len(list(cleaned.iter_attachments())), 1)


if __name__ == "__main__":
    unittest.main()