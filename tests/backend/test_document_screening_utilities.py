import json
import unittest

from backend.src.document_screening.tools.utilities import (
    add_polygon_metadata,
    merge_document_findings,
    parse_document_findings,
)
from document_screening_fixtures import valid_document_findings


class DocumentScreeningUtilitiesTests(unittest.TestCase):
    def test_parses_plain_or_fenced_json_document_findings(self):
        findings = valid_document_findings()

        self.assertEqual(parse_document_findings(json.dumps(findings)), findings)
        self.assertEqual(
            parse_document_findings("```json\n" + json.dumps(findings) + "\n```"),
            findings,
        )
        self.assertEqual(
            parse_document_findings("Result: " + json.dumps(findings)[:-1] + ",}"),
            findings,
        )

    def test_rejects_non_object_and_incomplete_document_findings(self):
        with self.assertRaisesRegex(ValueError, "JSON object"):
            parse_document_findings("[]")
        with self.assertRaisesRegex(ValueError, "invalid top-level structure"):
            parse_document_findings('{"completed_assessments": []}')
        with self.assertRaisesRegex(ValueError, "invalid top-level structure"):
            parse_document_findings('Result: {"completed_assessments": [],}')
        with self.assertRaisesRegex(ValueError, "not valid JSON"):
            parse_document_findings("not JSON")

    def test_ignores_unusable_coordinates_when_lpa_polygon_exists(self):
        findings = valid_document_findings()
        findings["location"]["coordinates"]["easting"] = "unknown"
        findings["location"]["coordinates"]["confidence"] = "not_stated"
        ring = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]

        parsed = parse_document_findings(json.dumps(findings))
        result = add_polygon_metadata({**parsed, "polygon": [ring]})

        self.assertEqual(result["polygon"]["polygon_source"], "lpa_maps")

    def test_accepts_empty_evidence_on_a_stated_field(self):
        findings = valid_document_findings()
        findings["scale_and_type_of_development"]["wind_solar"] = {
            "value": "None",
            "status": "stated",
            "confidence": "high",
            "reason": "No wind or solar proposals mentioned.",
            "evidence": [],
        }

        parsed = parse_document_findings(json.dumps(findings))

        self.assertEqual(
            parsed["scale_and_type_of_development"]["wind_solar"]["evidence"],
            [],
        )

    def test_accepts_descriptive_fields_with_missing_or_nullable_details(self):
        findings = valid_document_findings()
        field = findings["location"]["site_address"]
        field.update(confidence="null", evidence=[{"document_id": None, "quote": "Address"}])
        field.pop("reason")
        field["extra"] = "retained"

        parsed = parse_document_findings(json.dumps(findings))

        self.assertIsNone(parsed["location"]["site_address"]["confidence"])
        self.assertIsNone(parsed["location"]["site_address"]["evidence"][0]["document_id"])
        self.assertEqual(parsed["location"]["site_address"]["extra"], "retained")
        self.assertIn("location.site_address.evidence.document_id", parsed["screening_completeness"]["missing_fields"])

    def test_preserves_nullable_designated_site_details(self):
        findings = valid_document_findings()
        findings["site_and_surroundings"]["designated_sites_mentioned"] = [{
            "name": None,
            "type": "SSSI",
            "evidence": [{"document_id": None, "quote": "SSSI"}],
        }]

        parsed = parse_document_findings(json.dumps(findings))

        self.assertIsNone(parsed["site_and_surroundings"]["designated_sites_mentioned"][0]["name"])
        self.assertIsNone(parsed["site_and_surroundings"]["designated_sites_mentioned"][0]["evidence"][0]["document_id"])

    def test_records_missing_development_and_other_sections_without_inventing_values(self):
        findings = valid_document_findings()
        del findings["scale_and_type_of_development"]
        del findings["limitations"]

        parsed = parse_document_findings(json.dumps(findings))

        self.assertNotIn("scale_and_type_of_development", parsed)
        self.assertNotIn("limitations", parsed)
        self.assertEqual(parsed["screening_completeness"], {
            "complete": False,
            "missing_fields": ["limitations", "scale_and_type_of_development"],
            "incomplete_development_fields": ["scale_and_type_of_development"],
        })

    def test_records_incomplete_development_field(self):
        findings = valid_document_findings()
        del findings["scale_and_type_of_development"]["residential"]["value"]

        parsed = parse_document_findings(json.dumps(findings))

        self.assertNotIn("value", parsed["scale_and_type_of_development"]["residential"])
        self.assertEqual(parsed["screening_completeness"]["incomplete_development_fields"],
                         ["scale_and_type_of_development.residential"])

    def test_records_uncertain_or_inconsistent_development_values(self):
        for status, value in (("stated", " "), ("unclear", None), ("conflicting", None), ("not_stated", "None")):
            with self.subTest(status=status, value=value):
                findings = valid_document_findings()
                findings["scale_and_type_of_development"]["residential"].update(status=status, value=value)
                parsed = parse_document_findings(json.dumps(findings))
                self.assertIn("scale_and_type_of_development.residential",
                              parsed["screening_completeness"]["incomplete_development_fields"])

    def test_normalizes_string_null_in_descriptive_field_value(self):
        findings = valid_document_findings()
        findings["scale_and_type_of_development"]["residential"]["value"] = "null"
        parsed = parse_document_findings(json.dumps(findings))
        self.assertIsNone(parsed["scale_and_type_of_development"]["residential"]["value"])

    def test_rejects_missing_or_invalid_decision_signals(self):
        for invalid_signal in (None, {"classification": "unknown", "confidence": "high", "reason": "", "evidence": []}):
            with self.subTest(invalid_signal=invalid_signal):
                findings = valid_document_findings()
                if invalid_signal is None:
                    del findings["consultation_type_signals"]["amendment"]
                else:
                    findings["consultation_type_signals"]["amendment"] = invalid_signal
                with self.assertRaises(ValueError):
                    parse_document_findings(json.dumps(findings))

    def test_normalizes_numeric_coordinate_strings(self):
        findings = valid_document_findings()
        findings["location"]["coordinates"]["easting"] = "359926"
        findings["location"]["coordinates"]["northing"] = "322794.5"

        parsed = parse_document_findings(json.dumps(findings))

        self.assertEqual(parsed["location"]["coordinates"]["easting"], 359926)
        self.assertIs(type(parsed["location"]["coordinates"]["easting"]), int)
        self.assertEqual(parsed["location"]["coordinates"]["northing"], 322794.5)

    def test_accepts_not_stated_coordinates_when_lpa_polygon_exists(self):
        findings = valid_document_findings()
        findings["location"]["coordinates"] = {
            "easting": None,
            "northing": None,
            "grid_reference": None,
            "units": None,
            "crs": None,
            "status": "not_stated",
            "confidence": "not_stated",
            "reason": "No site coordinates were stated in the supplied documents.",
            "evidence": [],
        }
        ring = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]

        parsed = parse_document_findings(json.dumps(findings))
        result = add_polygon_metadata({**parsed, "polygon": [ring]})

        self.assertEqual(result["location"]["coordinates"]["status"], "not_stated")
        self.assertEqual(result["polygon"]["polygon_source"], "lpa_maps")

    def test_rejects_not_stated_coordinates_when_polygon_inference_is_required(self):
        final_output = {**valid_document_findings(), "polygon": []}
        final_output["location"]["coordinates"].update(
            easting=None,
            northing=None,
            status="not_stated",
            confidence=None,
            reason="No site coordinates were stated in the supplied documents.",
            evidence=[],
        )

        with self.assertRaisesRegex(ValueError, "coordinates must be stated with medium or high confidence"):
            add_polygon_metadata(final_output)

    def test_adds_lpa_polygon_provenance(self):
        ring = [[500000, 200000], [500100, 200000], [500100, 200100], [500000, 200000]]
        final_output = {"polygon": [ring]}

        result = add_polygon_metadata(final_output)

        self.assertEqual(result["polygon"], {
            "polygon": [ring],
            "polygon_source": "lpa_maps",
            "polygon_confidence": "high",
            "polygon_area_source": "lpa_polygon",
        })

    def test_infers_square_from_coordinates_and_site_area(self):
        final_output = {**valid_document_findings(), "polygon": []}

        result = add_polygon_metadata(final_output)

        self.assertEqual(result["polygon"], {
            "polygon": [[
                [499900.0, 199900.0],
                [500100.0, 199900.0],
                [500100.0, 200100.0],
                [499900.0, 200100.0],
                [499900.0, 199900.0],
            ]],
            "polygon_source": "document_coordinates",
            "polygon_confidence": "medium",
            "polygon_area_source": "document_site_area",
        })

    def test_inference_normalizes_case_and_punctuation_in_square_metre_units(self):
        for area_value in (
            "3391.00 Sq. metres",
            "3,391.00 SQ. METRES",
            "3391.00 sq metres.",
        ):
            with self.subTest(area_value=area_value):
                final_output = {**valid_document_findings(), "polygon": []}
                final_output["location"]["site_area"]["value"] = area_value

                result = add_polygon_metadata(final_output)

                ring = result["polygon"]["polygon"][0]
                width = ring[1][0] - ring[0][0]
                self.assertAlmostEqual(width * width, 3391.0)
                self.assertEqual(
                    result["polygon"]["polygon_area_source"], "document_site_area"
                )

    def test_inference_requires_usable_location_and_area(self):
        final_output = {**valid_document_findings(), "polygon": []}
        final_output["location"]["coordinates"]["confidence"] = "low"

        with self.assertRaisesRegex(ValueError, "coordinates must be stated with medium or high confidence"):
            add_polygon_metadata(final_output)

        final_output["location"]["coordinates"]["confidence"] = "high"
        final_output["location"]["site_area"]["status"] = "not_stated"
        final_output["location"]["site_area"]["value"] = None
        result = add_polygon_metadata(final_output)
        polygon = result["polygon"]
        self.assertEqual(polygon["polygon_confidence"], "low")
        self.assertEqual(polygon["polygon_area_source"], "default_5_hectares")
        self.assertAlmostEqual(
            (polygon["polygon"][0][1][0] - polygon["polygon"][0][0][0]) ** 2,
            50_000,
        )

    def test_inference_converts_acres_to_square_metres(self):
        final_output = {**valid_document_findings(), "polygon": []}
        final_output["location"]["site_area"]["value"] = "1 acre"

        result = add_polygon_metadata(final_output)

        ring = result["polygon"]["polygon"][0]
        width = ring[1][0] - ring[0][0]
        self.assertAlmostEqual(width * width, 4046.8564224)

    def test_merges_without_modifying_input_objects(self):
        lpa_metadata = {"reference": "25/03341/CDPN", "polygon": []}
        document_findings = valid_document_findings()

        result = merge_document_findings(lpa_metadata, document_findings)

        self.assertEqual(result, {
            "reference": "25/03341/CDPN",
            "polygon": [],
            **document_findings,
        })
        self.assertEqual(lpa_metadata, {"reference": "25/03341/CDPN", "polygon": []})
        self.assertEqual(document_findings, valid_document_findings())

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