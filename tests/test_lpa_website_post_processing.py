import unittest
from backend.src.case_intake.tools.lpa_website_post_processing import deterministic_post_processing_lpa_website


class LpaWebsitePostProcessingTests(unittest.TestCase):
    def setUp(self):
        self.mapping = [
            {
                "lpa_name": "Example",
                "assigned_current_lpa_name": "Example Council",
                "lpa_website": "https://planning.example.gov.uk/online-applications/",
            },
            {
                "lpa_name": "Other",
                "assigned_current_lpa_name": "Other Council",
                "lpa_website": "https://planning.example.gov.uk/other/",
            },
        ]

    def test_name_disambiguates_shared_host_and_preserves_original(self):
        # Use the LPA name to select between websites sharing a host.
        # Check that the matching URL is used and the original extraction preserved.
        original = {"value": "https://www.example.gov.uk", "status": "stated"}
        document = {"records": [{"extractions": {
            "lpa_name": {"value": "Example Council"},
            "lpa_website": dict(original),
        }}]}
        changed, warnings = deterministic_post_processing_lpa_website(document, self.mapping, "mapping.xlsx")
        website = document["records"][0]["extractions"]["lpa_website"]
        self.assertTrue(changed)
        self.assertEqual(warnings, [])
        self.assertEqual(website["value"], self.mapping[0]["lpa_website"])
        self.assertEqual(website["original_extraction"], original)

    def test_unspaced_original_name_disambiguates_shared_host(self):
        # Compare an extracted LPA name with an unspaced spreadsheet name.
        # Check that the name selects the correct website on a shared host.
        self.mapping[0]["lpa_name"] = "AdurWorthing"
        document = {"records": [{"extractions": {
            "lpa_name": "Adur Worthing", "lpa_website": "https://example.gov.uk",
        }}]}
        changed, warnings = deterministic_post_processing_lpa_website(document, self.mapping, "mapping.xlsx")
        self.assertTrue(changed)
        self.assertFalse(warnings)
        self.assertEqual(document["records"][0]["extractions"]["lpa_website"], self.mapping[0]["lpa_website"])

    def test_ambiguous_names_after_normalisation_are_not_replaced(self):
        # Compare a name against two spreadsheet names that normalise identically.
        # Check that the original website is retained with a warning.
        self.mapping[0]["lpa_name"] = "AdurWorthing"
        self.mapping[1]["lpa_name"] = "Adur Worthing"
        document = {"records": [{"extractions": {
            "lpa_name": "ADUR WORTHING", "lpa_website": "https://example.gov.uk",
        }}]}
        changed, warnings = deterministic_post_processing_lpa_website(document, self.mapping, "mapping.xlsx")
        self.assertFalse(changed)
        self.assertTrue(warnings)
        self.assertEqual(document["records"][0]["extractions"]["lpa_website"], "https://example.gov.uk")

    def test_ambiguous_missing_and_unrelated_websites_are_not_replaced(self):
        # Try ambiguous, missing, unrelated and malformed websites.
        # Check that each value is retained with a warning.
        for website in ("https://example.gov.uk", None, "https://example.gov.uk.attacker.com", "http://[bad"):
            with self.subTest(website=website):
                document = {"records": [{"extractions": {"lpa_website": website}}]}
                changed, warnings = deterministic_post_processing_lpa_website(document, self.mapping, "mapping.xlsx")
                self.assertFalse(changed)
                self.assertTrue(warnings)
                self.assertEqual(document["records"][0]["extractions"]["lpa_website"], website)

    def test_known_name_that_conflicts_with_website_is_not_replaced(self):
        # Provide an LPA name and website that match different authorities.
        # Check that the website is retained and a warning is returned.
        mapping = self.mapping + [{
            "lpa_name": "Elsewhere",
            "assigned_current_lpa_name": "Elsewhere Council",
            "lpa_website": "https://elsewhere.gov.uk/",
        }]
        website = "https://example.gov.uk"
        document = {"records": [{"extractions": {
            "lpa_name": "Elsewhere Council", "lpa_website": website,
        }}]}
        changed, warnings = deterministic_post_processing_lpa_website(document, mapping, "mapping.xlsx")
        self.assertFalse(changed)
        self.assertTrue(warnings)
        self.assertEqual(document["records"][0]["extractions"]["lpa_website"], website)

    def test_path_disambiguates_shared_host(self):
        # Use an extracted path to distinguish websites on a shared host.
        # Check that the matching spreadsheet URL is selected.
        document = {"records": [{"extractions": {
            "lpa_website": "https://planning.example.gov.uk/other/details",
        }}]}
        changed, warnings = deterministic_post_processing_lpa_website(document, self.mapping, "mapping.xlsx")
        self.assertTrue(changed)
        self.assertFalse(warnings)
        self.assertEqual(document["records"][0]["extractions"]["lpa_website"], self.mapping[1]["lpa_website"])

    def test_conflicting_name_and_path_do_not_replace_website(self):
        # Provide an LPA name and URL path that match different spreadsheet rows.
        # Check that the original website is retained with a warning.
        website = "https://planning.example.gov.uk/other/details"
        document = {"records": [{"extractions": {
            "lpa_name": "Example Council", "lpa_website": website,
        }}]}
        changed, warnings = deterministic_post_processing_lpa_website(document, self.mapping, "mapping.xlsx")
        self.assertFalse(changed)
        self.assertTrue(warnings)
        self.assertEqual(document["records"][0]["extractions"]["lpa_website"], website)

    def test_conflicting_name_status_does_not_replace_website(self):
        # Mark the extracted LPA name as conflicting.
        # Check that the website is retained and a warning is returned.
        website = "https://planning.example.gov.uk/online-applications/extra"
        document = {"records": [{"extractions": {
            "lpa_name": {"value": "Example Council", "status": "conflicting"},
            "lpa_website": website,
        }}]}
        changed, warnings = deterministic_post_processing_lpa_website(document, self.mapping, "mapping.xlsx")
        self.assertFalse(changed)
        self.assertTrue(warnings)
        self.assertEqual(document["records"][0]["extractions"]["lpa_website"], website)


if __name__ == "__main__":
    unittest.main()