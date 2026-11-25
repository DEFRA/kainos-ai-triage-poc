import unittest

from backend.src.spatial_screening.tools.utilities import (
	check_strategic_irz_codes,
	map_internal_irz_codes,
	parse_irz_category_answers,
	parse_internal_irz_codes,
)


INTERNAL_LINK_WITH_THREE_RECORDS = (
	"https://irz.geodata.org.uk/IRZ_internal/IRZ.php?location=521708,444920"
	"&point_txt=%20(API%20geometry%20centre)"
	"&irzcode=1002380,0321000432040,3,;"
	"1005735,0300000500000,5,;"
	"UK9006171,0000000000000,B1,21103"
)
INTERNAL_LINK_WITH_MULTIPLE_OPTIONS = (
	"https://irz.geodata.org.uk/IRZ_internal/IRZ.php?location=520045,445317"
	"&point_txt=%20(API%20geometry%20centre)"
	"&irzcode=1005735,0300000000000,7,;"
	"1002380,0111344322342,2,;"
	"UK9006171,0000000000000,B1,21103"
)


class InternalIRZCodeUtilityTests(unittest.TestCase):
	def test_repairs_and_validates_irz_model_json(self):
		self.assertEqual(parse_irz_category_answers('Result: {"answers": [],}', []), [])
		with self.assertRaisesRegex(ValueError, "answers.*array"):
			parse_irz_category_answers('Result: {"answers": "invalid",}', [])

	def test_parse_internal_link_extracts_each_record(self):
		records = parse_internal_irz_codes(INTERNAL_LINK_WITH_THREE_RECORDS)

		self.assertEqual(len(records), 3)
		self.assertEqual(records[0], {
			"site_or_designation_id": "1002380",
			"irz_code": "0321000432040",
			"irz_zone_selector": "3",
			"advice_reference": None,
		})
		self.assertEqual(records[2]["advice_reference"], "21103")

	def test_parser_and_mapping_keep_lowest_nonzero_option_and_zero(self):
		records = parse_internal_irz_codes(INTERNAL_LINK_WITH_MULTIPLE_OPTIONS)
		categories = map_internal_irz_codes(records)

		self.assertEqual(len(categories), 13)
		infrastructure = next(
			category for category in categories
			if category["name"] == "Infrastructure"
		)
		self.assertEqual(
			[value["value"] for value in infrastructure["options"]],
			[1, 0],
		)
		self.assertEqual(
			infrastructure["options"][-1],
			{"value": 0, "text": "Not applicable"},
		)

	def test_each_category_uses_minimum_nonzero_or_zero_when_all_are_zero(self):
		for values, expected in (
			([1, 3], [1, 0]),
			([0, 3], [3, 0]),
			([2, 3], [2, 0]),
			([0, 0], [0]),
		):
			with self.subTest(values=values):
				records = [
					{"irz_code": "0" + str(value) + "0" * 11}
					for value in values
				]
				categories = map_internal_irz_codes(records)
				infrastructure = next(
					category for category in categories
					if category["name"] == "Infrastructure"
				)
				self.assertEqual(
					[option["value"] for option in infrastructure["options"]],
					expected,
				)

	def test_strategic_lookup_uses_advice_reference_not_location_digits(self):
		records = parse_internal_irz_codes(INTERNAL_LINK_WITH_THREE_RECORDS)

		matches = check_strategic_irz_codes(records)

		self.assertTrue(any(record["gis_ref"] == "21103" for record in matches))

	def test_parser_rejects_malformed_development_codes(self):
		malformed_link = (
			"https://irz.test/IRZ.php?irzcode=1002380,032100043204X,3,"
		)

		with self.assertRaisesRegex(ValueError, "13 digits"):
			parse_internal_irz_codes(malformed_link)


if __name__ == "__main__":
	unittest.main()