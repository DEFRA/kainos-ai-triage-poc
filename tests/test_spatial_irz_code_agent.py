import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.src.spatial_screening.implementations.irz_code_agent import (
	run_irz_code_spatial_screening,
)
from backend.src.spatial_screening.tools.utilities import (
	map_internal_irz_codes,
	parse_internal_irz_codes,
)


INTERNAL_LINK = (
	"https://irz.geodata.org.uk/IRZ_internal/IRZ.php?location=520045,445317"
	"&point_txt=%20(API%20geometry%20centre)"
	"&irzcode=1005735,0300000000000,7,;"
	"1002380,0111344322342,2,;"
	"UK9006171,0000000000000,B1,21103"
)
POLYGON = [
	[521707.989, 444919.962],
	[521710.0, 444919.962],
	[521707.989, 444919.962],
]


def model_response(categories, invalid_infrastructure_value=False):
	answers = []
	for category in categories:
		if category["name"] == "Infrastructure":
			option = next(
				option for option in category["options"]
				if option["value"] == 1
			)
			confidence = "Medium"
		else:
			option = category["options"][-1]
			confidence = "High"
		answers.append({
			"category": category["name"],
			"value": 99 if invalid_infrastructure_value and category["name"] == "Infrastructure" else option["value"],
			"text": option["text"],
			"confidence": confidence,
			"reasoning": "Selected from the supplied evidence.",
			"supporting_evidence": [],
			"conflicting_evidence": [],
			"missing_information": [],
		})
	return json.dumps({"answers": answers})


class IRZCodeSpatialAgentTests(unittest.TestCase):
	def _run_with_response(
		self,
		response,
		implementation_config_path=None,
		built_up_coverage=60.0,
	):
		events = []
		layer_intersections = {
			"Best_and_most_versatile_land": True,
			"Protected_Landscape_Impact_Risk_Zones": False,
		}
		def fake_intersect_layer(data_source, layer_name, *_args, **_kwargs):
			events.append(layer_name)
			if layer_name == "os_open_built_up_areas":
				return {"intersection_percentage": built_up_coverage}
			return {"intersects": layer_intersections[layer_name]}

		with patch(
			"backend.src.spatial_screening.implementations.irz_code_agent.intersect_polygon_with_layer"
		) as intersect_layer_mock, patch(
			"backend.src.spatial_screening.implementations.irz_code_agent.InternalIRZClient"
		) as irz_client_class, patch(
			"backend.src.spatial_screening.implementations.irz_code_agent.LLMClient"
		) as intelligence_class:
			intersect_layer_mock.side_effect = fake_intersect_layer
			irz_client_class.return_value.get_internal_link.side_effect = lambda _: (
				events.append("irz") or INTERNAL_LINK
			)
			intelligence = intelligence_class.return_value.__enter__.return_value
			intelligence.generate.return_value = response
			intelligence.config.provider = "bedrock"
			intelligence.config.model = "test-model"
			result = run_irz_code_spatial_screening(
				POLYGON,
				{"lpa": {"description": "A planning proposal"}},
				"model.yaml",
				implementation_config_path=implementation_config_path,
			)
			prompt = intelligence.generate.call_args.args[0]
		return result, prompt, events

	def test_code_table_implementation_returns_mapped_evidence_and_review_flags(self):
		categories = map_internal_irz_codes(parse_internal_irz_codes(INTERNAL_LINK))
		response = model_response(categories)

		result, prompt, events = self._run_with_response(response)

		self.assertEqual(result["internal_link"], INTERNAL_LINK)
		self.assertEqual(len(result["irz_codes"]), 3)
		self.assertTrue(result["is_strategic_consultation"])
		self.assertEqual(result["provider"], "bedrock")
		self.assertEqual(result["model"], "test-model")
		self.assertEqual(result["human_review_confidence_threshold"], "High")
		self.assertEqual(events, [
			"os_open_built_up_areas",
			"Best_and_most_versatile_land",
			"Protected_Landscape_Impact_Risk_Zones",
			"irz",
		])
		self.assertEqual(result["geospatial_checks"]["urban_rural"], {
			"classification": "urban",
			"built_up_area_coverage_percentage": 60.0,
			"threshold_percentage": 50.0,
			"source_layer": "os_open_built_up_areas",
		})
		self.assertEqual(
			result["geospatial_checks"]["best_and_most_versatile_land"],
			{
				"intersects": True,
				"source_layer": "Best_and_most_versatile_land",
			},
		)
		self.assertEqual(
			result["geospatial_checks"]["protected_landscape_impact_risk_zones"],
			{
				"intersects": False,
				"source_layer": "Protected_Landscape_Impact_Risk_Zones",
			},
		)
		infrastructure = next(
			answer for answer in result["answers"]
			if answer["category"] == "Infrastructure"
		)
		self.assertEqual(infrastructure["value"], 1)
		self.assertTrue(infrastructure["human_review_required"])
		self.assertTrue(result["human_review_required"])
		self.assertIn("Pipelines and underground cables", prompt)
		self.assertNotIn('"irz_code"', prompt)
		context_in_prompt = json.loads(prompt.split("Consultation context:\n", 1)[1])
		self.assertEqual(
			context_in_prompt["spatial_screening"]["settlement_type"], "urban"
		)
		self.assertNotIn(
			"best_and_most_versatile_land_intersects",
			context_in_prompt["spatial_screening"],
		)
		self.assertNotIn(
			"protected_landscape_intersects",
			context_in_prompt["spatial_screening"],
		)

	def test_medium_threshold_does_not_review_medium_confidence(self):
		categories = map_internal_irz_codes(parse_internal_irz_codes(INTERNAL_LINK))
		response = model_response(categories)
		with TemporaryDirectory() as directory:
			config_path = Path(directory) / "spatial_screening.yaml"
			config_path.write_text(
				"spatial_screening:\n"
				"  human_review_confidence_threshold: Medium\n"
				"  urban_built_up_coverage_threshold_percent: 50\n"
				"internal_irz:\n"
				"  request_timeout_seconds: 30\n",
				encoding="utf-8",
			)
			result, _, _ = self._run_with_response(response, config_path)

		self.assertEqual(result["human_review_confidence_threshold"], "Medium")
		self.assertFalse(result["human_review_required"])
		self.assertTrue(all(
			not answer["human_review_required"] for answer in result["answers"]
		))

	def test_configured_urban_threshold_classifies_coverage_below_threshold_as_rural(self):
		categories = map_internal_irz_codes(parse_internal_irz_codes(INTERNAL_LINK))
		response = model_response(categories)
		with TemporaryDirectory() as directory:
			config_path = Path(directory) / "spatial_screening.yaml"
			config_path.write_text(
				"spatial_screening:\n"
				"  human_review_confidence_threshold: High\n"
				"  urban_built_up_coverage_threshold_percent: 75\n"
				"internal_irz:\n"
				"  request_timeout_seconds: 30\n",
				encoding="utf-8",
			)
			result, prompt, _ = self._run_with_response(
				response,
				implementation_config_path=config_path,
				built_up_coverage=60.0,
			)
			boundary_result, _, _ = self._run_with_response(
				response,
				implementation_config_path=config_path,
				built_up_coverage=75.0,
			)

		self.assertEqual(
			result["geospatial_checks"]["urban_rural"]["classification"], "rural"
		)
		context_in_prompt = json.loads(prompt.split("Consultation context:\n", 1)[1])
		self.assertEqual(
			context_in_prompt["spatial_screening"]["settlement_type"], "rural"
		)
		self.assertEqual(
			result["geospatial_checks"]["urban_rural"]["threshold_percentage"],
			75.0,
		)
		self.assertEqual(
			boundary_result["geospatial_checks"]["urban_rural"]["classification"],
			"urban",
		)

	def test_invalid_human_review_threshold_is_rejected(self):
		with TemporaryDirectory() as directory:
			config_path = Path(directory) / "spatial_screening.yaml"
			config_path.write_text(
				"spatial_screening:\n"
				"  human_review_confidence_threshold: Low\n"
				"internal_irz:\n"
				"  request_timeout_seconds: 30\n",
				encoding="utf-8",
			)

			with self.assertRaisesRegex(ValueError, "'Medium' or 'High'"):
				run_irz_code_spatial_screening(
					POLYGON,
					{},
					"model.yaml",
					implementation_config_path=config_path,
				)

	def test_code_table_implementation_rejects_unoffered_model_value(self):
		categories = map_internal_irz_codes(parse_internal_irz_codes(INTERNAL_LINK))
		response = model_response(categories, invalid_infrastructure_value=True)

		with self.assertRaisesRegex(ValueError, "was not offered"):
			self._run_with_response(response)


if __name__ == "__main__":
	unittest.main()