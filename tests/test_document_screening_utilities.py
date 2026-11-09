import json
import unittest

from backend.src.document_screening.tools.utilities import (
    merge_document_findings,
    parse_document_findings,
)


class DocumentScreeningUtilitiesTests(unittest.TestCase):
    def test_parses_plain_or_fenced_json_document_findings(self):
        findings = {
            "completed_assessments": [],
            "planning_permissions": [],
            "location": {"coordinates": [], "addresses": []},
            "other": [],
            "limitations": [],
        }

        self.assertEqual(parse_document_findings(json.dumps(findings)), findings)
        self.assertEqual(
            parse_document_findings("```json\n" + json.dumps(findings) + "\n```"),
            findings,
        )

    def test_rejects_non_object_and_incomplete_document_findings(self):
        with self.assertRaisesRegex(ValueError, "JSON object"):
            parse_document_findings("[]")
        with self.assertRaisesRegex(ValueError, "invalid top-level structure"):
            parse_document_findings('{"completed_assessments": []}')
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            parse_document_findings("not JSON")

    def test_merges_without_modifying_input_objects(self):
        lpa_metadata = {"reference": "25/03341/CDPN", "polygon": []}
        document_findings = {"completed_assessments": []}

        result = merge_document_findings(lpa_metadata, document_findings)

        self.assertEqual(result, {
            "reference": "25/03341/CDPN",
            "polygon": [],
            "completed_assessments": [],
        })
        self.assertEqual(lpa_metadata, {"reference": "25/03341/CDPN", "polygon": []})
        self.assertEqual(document_findings, {"completed_assessments": []})

    def test_rejects_overlapping_keys_without_overwriting(self):
        with self.assertRaisesRegex(ValueError, "reference"):
            merge_document_findings(
                {"reference": "website value"},
                {"reference": "document value"},
            )

    def test_requires_json_objects(self):
        with self.assertRaisesRegex(TypeError, "JSON objects"):
            merge_document_findings({"reference": "case"}, [])


if __name__ == "__main__":
    unittest.main()