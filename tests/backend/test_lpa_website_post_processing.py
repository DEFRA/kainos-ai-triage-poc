import json
import os
import unittest
from pathlib import Path
from unittest.mock import Mock
from backend.src.case_intake.tools.email_extractor import load_idox_mapping
from backend.src.case_intake.tools.lpa_website_post_processing import deterministic_post_processing_lpa_website, llm_post_processing_lpa_website
from backend.src.utilities.llm_client import LLMClient


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


MAPPING_PATH = Path(__file__).resolve().parents[2] / "data" / "lpa_websites_post_processing_lookup.xlsx"

# Uses old LPA names, i.e. lpa_name in lookup, i.e. before any reorganisation of LPAs
EXAMPLES = [
    ("MidKent", "https://pa.midkent.gov.uk/online-applications/"),
    ("Chester", "https://pa.cheshirewestandchester.gov.uk/online-applications/"),
    ("EastSuffolk", "Public Access."),
    ("West Northamptonshire", "https://wnc.planning-register.co.uk/"),
    ("BaberghMidSuffolk", "http://www.midsuffolk.gov.uk/planning/development-management/application-search-and-comment/search-for applications/"),
    ("Cornwall", "http://planning.cornwall.gov.uk/online-applications/"),
    ("Sevenoaks", "https://pa.sevenoaks.gov.uk/online-applications/"),
    ("Sevenoaks", "https://pa.sevenoaks.gov.uk/online-applications/"),
    ("StHelens", "https://publicaccess.sthelens.gov.uk/online-applications/"),
    ("Sevenoaks", "https://pa.sevenoaks.gov.uk/online-applications/"),
    ("Cambridge", "https://applications.greatercambridgeplanning.org/online-applications/"),
    ("Southwark", "https://planning.southwark.gov.uk/online-applications/"),
    ("Hertsmere", "http://www6.hertsmere.gov.uk/online-applications/"),
    ("Luton", "https://planning.luton.gov.uk/online-applications/"),
    ("Craven", "https://publicaccess.northyorks.gov.uk"),
    ("WestSuffolk", "https://planning.westsuffolk.gov.uk/online-applications/"),
    ("EastRiding", "https://newplanningaccess.eastriding.gov.uk/newplanningaccess/"),
    ("EastDevon", "https://planning.eastdevon.gov.uk/online-applications/"),
    ("Rushcliffe", "https://planningon-line.rushcliffe.gov.uk/online-applications/"),
    ("SouthStaffordshire", "https://www.sstaffs.gov/PublicAccess"),
    ("EastHertfordshire", "https://publicaccess.eastherts.gov.uk/online-applications/"),
    ("MidKent", "www.swale.gov.uk"),
    ("Carlisle", "https://cumberland.gov.uk"),
    ("Craven", "www.northyorks.gov.uk"),
    ("Cornwall", "http://planning.cornwall.gov.uk/online-applications/"),
    ("Craven", "https://publicaccess.northyorks.gov.uk/online-applications/"),
    ("Sevenoaks", "https://pa.sevenoaks.gov.uk/online-applications/"),
    ("Dartford", "http://publicaccess.dartford.gov.uk/online-applications/"),
    ("Craven", "www.northyorks.gov.uk"),
    ("Tonbridge", "https://publicaccess.tmbc.gov.uk/online-applications/"),
    ("Cambridge", "https://applications.greatercambridgeplanning.org/online-applications/"),
    ("EastHampshire", "http://planningpublicaccess.easthants.gov.uk/online-applications/"),
    ("Newham", "https://pa.newham.gov.uk/online-applications/"),
    ("EastRiding", "https://newplanningaccess.eastriding.gov.uk/newplanningaccess/"),
    ("Sevenoaks", "https://pa.sevenoaks.gov.uk/online-applications/"),
    ("EastRiding", "https://newplanningaccess.eastriding.gov.uk/newplanningaccess/"),
    ("Northumberland", "https://publicaccess.northumberland.gov.uk/online-applications/"),
    ("Tonbridge", "https://publicaccess.tmbc.gov.uk/online-applications/"),
    ("Hertsmere", "http://www6.hertsmere.gov.uk/online-applications/"),
    ("Stroud", "https://publicaccess.stroud.gov.uk/online-applications/search.do?action=simple&searchType=Application"),
    ("Sevenoaks", "https://pa.sevenoaks.gov.uk/online-applications/"),
    ("EastLindsey", "http://publicaccess.e-lindsey.gov.uk/online-applications/"),
    ("Oxford", "https://public.oxford.gov.uk/online-applications/"),
]


class LlmLpaWebsitePostProcessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mapping = load_idox_mapping(MAPPING_PATH)

    def test_supplied_website_examples_use_selected_canonical_rows(self):
        for name, website in EXAMPLES:
            with self.subTest(name=name, website=website):
                document = {"records": [{"extractions": {
                    "lpa_name": name, "lpa_website": {"value": website, "status": "stated"},
                }}]}
                matches = [index for index, row in enumerate(self.mapping, 1) if row["lpa_name"] == name]
                self.assertEqual(len(matches), 0 if name in (None, "West Northamptonshire") else 1)
                client = Mock()
                client.generate.return_value = json.dumps({"row": matches[0] if matches else None})
                changed, warnings = llm_post_processing_lpa_website(document, self.mapping, MAPPING_PATH.name, client)
                result = document["records"][0]["extractions"]["lpa_website"]
                if matches:
                    expected = self.mapping[matches[0] - 1]["lpa_website"]
                    self.assertEqual(result["value"], expected)
                    self.assertEqual(changed, website != expected)
                    self.assertFalse(warnings)
                    if changed:
                        self.assertEqual(result["original_extraction"]["value"], website)
                        self.assertEqual(result["evidence"][0]["source"], MAPPING_PATH.name)
                    prompt = json.loads(client.generate.call_args.args[0])
                    self.assertEqual(prompt["extracted_lpa_name"], name)
                    self.assertEqual(prompt["extracted_lpa_website"], website)
                    self.assertEqual(prompt["lookup_rows"][matches[0] - 1]["assigned_current_lpa_name"], self.mapping[matches[0] - 1]["assigned_current_lpa_name"])
                else:
                    self.assertFalse(changed)
                    self.assertTrue(warnings)
                    self.assertEqual(result["value"], website)
                    if name is None:
                        client.generate.assert_not_called()

    def test_current_name_and_missing_website_are_supported(self):
        index, row = next((index, row) for index, row in enumerate(self.mapping, 1) if row["lpa_name"] == "Chester")
        client = Mock()
        client.generate.return_value = json.dumps({"row": index})
        for website in (None, {"value": None, "status": "not_stated"}):
            with self.subTest(website=website):
                document = {"records": [{"extractions": {
                    "lpa_name": {"value": row["assigned_current_lpa_name"], "status": "stated"},
                    "lpa_website": website,
                }}]}
                changed, warnings = llm_post_processing_lpa_website(document, self.mapping, MAPPING_PATH.name, client)
                self.assertTrue(changed)
                self.assertFalse(warnings)
                self.assertEqual(document["records"][0]["extractions"]["lpa_website"]["value"], row["lpa_website"])

    def test_conflicting_names_and_failed_or_invalid_selections_leave_website_unchanged(self):
        for response in ('{"row": null}', '{"row": 0}', '{"row": 9999}', '{"row": true}', '{"row": "1"}', '{}', 'no JSON'):
            with self.subTest(response=response):
                client = Mock()
                client.generate.return_value = response
                document = {"records": [{"extractions": {"lpa_name": "Example", "lpa_website": "original"}}]}
                changed, warnings = llm_post_processing_lpa_website(document, self.mapping, MAPPING_PATH.name, client)
                self.assertFalse(changed)
                self.assertTrue(warnings)
                self.assertEqual(document["records"][0]["extractions"]["lpa_website"], "original")
        for name in (None, "", {"value": "Example", "status": "conflicting"}, {"value": "Example", "status": "unclear"}):
            with self.subTest(name=name):
                client = Mock()
                document = {"records": [{"extractions": {"lpa_name": name, "lpa_website": "original"}}]}
                changed, warnings = llm_post_processing_lpa_website(document, self.mapping, MAPPING_PATH.name, client)
                self.assertFalse(changed)
                self.assertTrue(warnings)
                client.generate.assert_not_called()

    def test_model_failure_does_not_prevent_later_records_being_processed(self):
        client = Mock()
        client.generate.side_effect = [RuntimeError("model unavailable"), '{"row": 1}']
        document = {"records": [
            {"extractions": {"lpa_name": "First", "lpa_website": "original"}},
            {"extractions": {"lpa_name": "Second", "lpa_website": "original"}},
        ]}
        changed, warnings = llm_post_processing_lpa_website(document, self.mapping, MAPPING_PATH.name, client)
        self.assertTrue(changed)
        self.assertIn("model unavailable", warnings[0])
        self.assertEqual(document["records"][0]["extractions"]["lpa_website"], "original")
        self.assertEqual(document["records"][1]["extractions"]["lpa_website"], self.mapping[0]["lpa_website"])

    @unittest.skipUnless(os.environ.get("LPA_WEBSITE_MODEL_CONFIG"), "Set LPA_WEBSITE_MODEL_CONFIG to run live model evaluation")
    def test_supplied_examples_with_live_model(self):
        with LLMClient(os.environ["LPA_WEBSITE_MODEL_CONFIG"]) as client:
            for name, website in EXAMPLES:
                with self.subTest(name=name, website=website):
                    document = {"records": [{"extractions": {"lpa_name": name, "lpa_website": website}}]}
                    matches = [row for row in self.mapping if row["lpa_name"] == name]
                    self.assertEqual(len(matches), 0 if name in (None, "West Northamptonshire") else 1)
                    _, warnings = llm_post_processing_lpa_website(document, self.mapping, MAPPING_PATH.name, client)
                    expected = matches[0]["lpa_website"] if matches else website
                    self.assertEqual(document["records"][0]["extractions"]["lpa_website"], expected)
                    self.assertEqual(bool(warnings), not bool(matches))


if __name__ == "__main__":
    unittest.main()