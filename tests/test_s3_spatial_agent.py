import ast
import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from backend.src.spatial_screening.implementations import irz_code_agent
from backend.src.spatial_screening.implementations.s3_spatial_agent import (
	run_s3_spatial_screening,
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
CONFIG = (
	"spatial_screening:\n"
	"  human_review_confidence_threshold: High\n"
	"  urban_built_up_coverage_threshold_percent: 50\n"
)
S3_MODULE = "backend.src.spatial_screening.implementations.s3_spatial_agent"
IRZ_MODULE = "backend.src.spatial_screening.implementations.irz_code_agent"


def model_response(categories):
	answers = []
	for category in categories:
		if category["name"] == "Infrastructure":
			option = next(option for option in category["options"] if option["value"] == 1)
			confidence = "Medium"
		else:
			option = category["options"][-1]
			confidence = "High"
		answers.append({
			"category": category["name"],
			"value": option["value"],
			"text": option["text"],
			"confidence": confidence,
			"reason": "Selected from the supplied evidence.",
			"evidence": [],
		})
	return json.dumps({"answers": answers})


class S3SpatialAgentTests(unittest.TestCase):
	def setUp(self):
		directory = TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		self.config_path = Path(directory.name) / "spatial_screening.yaml"
		self.config_path.write_text(CONFIG, encoding="utf-8")
		categories = map_internal_irz_codes(parse_internal_irz_codes(INTERNAL_LINK))
		self.response = model_response(categories)
		self.context = {"case_data": {"proposal": "Solar farm"}, "spatial_screening": {"note": "x"}}
		self.s3 = Mock()
		self.s3.put_object.return_value = {"VersionId": "version-1"}

	def _patch_pipeline(self, module, coverage=60.0):
		def fake_intersect(_data_source, layer_name, *_args, **kwargs):
			if layer_name == "os_open_built_up_areas":
				return {"intersection_percentage": coverage}
			if layer_name == "Best_and_most_versatile_land":
				return {"intersects": True, "intersection_area_m2": 234000.0}
			return {"intersects": False}

		intersect = self.enterContext(
			patch(f"{module}.intersect_polygon_with_layer", side_effect=fake_intersect)
		)
		irz_client = self.enterContext(patch(f"{module}.InternalIRZClient"))
		irz_client.return_value.get_internal_link.return_value = INTERNAL_LINK
		llm_class = self.enterContext(patch(f"{module}.LLMClient"))
		llm = llm_class.return_value.__enter__.return_value
		llm.generate.return_value = self.response
		llm.config.provider = "bedrock"
		llm.config.model = "test-model"
		return llm, intersect, irz_client

	def _run(self, **overrides):
		arguments = {
			"bucket": "screening-bucket",
			"prefix": "/case-1/run-1/",
			"s3_client": self.s3,
			"implementation_config_path": self.config_path,
		}
		arguments.update(overrides)
		return run_s3_spatial_screening(POLYGON, self.context, "model.yaml", **arguments)

	def test_uploads_result_and_returns_storage_location(self):
		self._patch_pipeline(S3_MODULE)

		result = self._run()

		self.s3.put_object.assert_called_once()
		kwargs = self.s3.put_object.call_args.kwargs
		self.assertEqual(kwargs["Bucket"], "screening-bucket")
		self.assertEqual(kwargs["Key"], "case-1/run-1/spatial_screening.json")
		stored = json.loads(kwargs["Body"])
		self.assertEqual(stored, {key: value for key, value in result.items() if key != "storage"})
		self.assertEqual(
			result["storage"],
			{
				"bucket": "screening-bucket",
				"key": "case-1/run-1/spatial_screening.json",
				"version": "version-1",
				"s3_uri": "s3://screening-bucket/case-1/run-1/spatial_screening.json",
			},
		)

	def test_result_matches_irz_code_agent_apart_from_storage(self):
		self._patch_pipeline(S3_MODULE)
		s3_result = self._run()
		self._patch_pipeline(IRZ_MODULE)
		irz_result = irz_code_agent.run_irz_code_spatial_screening(
			POLYGON,
			self.context,
			"model.yaml",
			implementation_config_path=self.config_path,
		)

		self.assertEqual({k: v for k, v in s3_result.items() if k != "storage"}, irz_result)
		self.assertTrue(s3_result["human_review_required"])

	def test_context_is_not_mutated(self):
		self._patch_pipeline(S3_MODULE)
		original_context = copy.deepcopy(self.context)

		self._run()

		self.assertEqual(self.context, original_context)

	def test_urban_threshold_classifies_coverage_as_rural_below_threshold(self):
		llm, _intersect, _irz = self._patch_pipeline(S3_MODULE, coverage=49.9)

		result = self._run()

		self.assertEqual(
			result["geospatial_checks"]["urban_rural"]["classification"], "rural"
		)
		prompt = llm.generate.call_args.args[0]
		self.assertIn('"settlement_type": "rural"', prompt)

	def test_invalid_storage_arguments_fail_before_screening(self):
		for bucket, prefix in (("", "run"), ("bucket", "///"), (None, "run")):
			with self.subTest(bucket=bucket, prefix=prefix):
				_llm, intersect, irz_client = self._patch_pipeline(S3_MODULE)

				with self.assertRaises(ValueError):
					self._run(bucket=bucket, prefix=prefix)

				intersect.assert_not_called()
				irz_client.assert_not_called()
				self.s3.put_object.assert_not_called()

	def test_creates_default_s3_client_when_not_injected(self):
		self._patch_pipeline(S3_MODULE)
		with patch(f"{S3_MODULE}.create_s3_client", return_value=self.s3) as factory:
			self._run(s3_client=None)

		factory.assert_called_once_with()
		self.s3.put_object.assert_called_once()

	def test_upload_failure_names_destination_and_keeps_cause(self):
		self._patch_pipeline(S3_MODULE)
		cause = OSError("bucket unavailable")
		self.s3.put_object.side_effect = cause

		with self.assertRaisesRegex(
			RuntimeError, "s3://screening-bucket/case-1/run-1/spatial_screening.json"
		) as raised:
			self._run()

		self.assertIs(raised.exception.__cause__, cause)

	def test_screening_failure_does_not_upload(self):
		llm, _intersect, _irz = self._patch_pipeline(S3_MODULE)
		llm.generate.side_effect = RuntimeError("model unavailable")

		with self.assertRaisesRegex(RuntimeError, "model unavailable"):
			self._run()

		self.s3.put_object.assert_not_called()

	def test_module_does_not_import_irz_code_agent(self):
		source_path = Path(irz_code_agent.__file__).with_name("s3_spatial_agent.py")
		tree = ast.parse(source_path.read_text(encoding="utf-8"))
		imported_modules = [
			node.module or ""
			for node in ast.walk(tree)
			if isinstance(node, ast.ImportFrom)
		]

		self.assertFalse(any("irz_code_agent" in module for module in imported_modules))


if __name__ == "__main__":
	unittest.main()
