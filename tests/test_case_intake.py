import unittest

from backend.src.email_intake import run_case_intake


class CaseIntakeTests(unittest.TestCase):
    def test_returns_supplied_case_data_unchanged(self):
        case_data = {
            "reference_id": "26/00123/FUL",
            "lpa_email": "planning@example.org",
            "lpa_website": "https://example.org/planning",
            "lpa_name": "Example Council",
            "dates": {
                "received": "2026-11-10",
                "response_due": "2026-12-01",
            },
            "additional_information": ["Preserve any supplied fields"],
        }

        self.assertIs(run_case_intake(case_data), case_data)

    def test_returns_empty_case_data_unchanged(self):
        case_data = {}

        self.assertIs(run_case_intake(case_data), case_data)


if __name__ == "__main__":
    unittest.main()