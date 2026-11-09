import json
import math
from pathlib import Path
from typing import Optional, Union

import yaml

from backend.src.utilities.llm_client import LLMClient
from ..tools.geospatial import intersect_polygon_with_layer
from ..tools.internal_irz_client import InternalIRZClient
from ..tools.utilities import (
	check_strategic_irz_codes,
	map_internal_irz_codes,
	parse_internal_irz_codes,
	parse_irz_category_answers,
)


_ANSWER_IRZ_CATEGORY_PROMPT = (
	Path(__file__).parent.parent / "prompts" / "answer_irz_category.md"
)
_DEFAULT_IMPLEMENTATION_CONFIG = (
	Path(__file__).resolve().parents[4]
	/ "environment"
	/ "configs"
	/ "spatial_screening.yaml"
)
_BUILT_UP_AREAS_DATA_SOURCE = (
	Path(__file__).resolve().parents[4]
	/ "data"
	/ "OS_Open_Built_Up_Areas_GeoPackage"
	/ "os_open_built_up_areas.gpkg"
)
_BUILT_UP_AREAS_LAYER = "os_open_built_up_areas"
_BEST_AND_MOST_VERSATILE_LAND_DATA_SOURCE = (
	Path(__file__).resolve().parents[4]
	/ "data"
	/ "Best_and_most_versatile_land.gdb"
)
_BEST_AND_MOST_VERSATILE_LAND_LAYER = "Best_and_most_versatile_land"
_PROTECTED_LANDSCAPE_DATA_SOURCE = (
	Path(__file__).resolve().parents[4]
	/ "data"
	/ "Protected_Landscape_Impact_Risk_Zones.gdb"
)
_PROTECTED_LANDSCAPE_LAYER = "Protected_Landscape_Impact_Risk_Zones"
_CONFIDENCE_LEVELS = {"Low": 0, "Medium": 1, "High": 2}


def _load_spatial_screening_settings(config_path: Union[str, Path]) -> dict[str, object]:
	config_path = Path(config_path)
	try:
		with config_path.open(encoding="utf-8") as config_file:
			config = yaml.safe_load(config_file)
	except yaml.YAMLError as error:
		raise ValueError(
			f"Invalid spatial-screening YAML in {config_path}: {error}"
		) from error

	if not isinstance(config, dict):
		raise ValueError(f"Spatial-screening config {config_path} must be a YAML mapping.")
	settings = config.get("spatial_screening", {})
	if not isinstance(settings, dict):
		raise ValueError("'spatial_screening' config section must be a mapping.")
	allowed_settings = {
		"human_review_confidence_threshold",
		"urban_built_up_coverage_threshold_percent",
	}
	if settings.keys() - allowed_settings:
		raise ValueError(
			"Spatial-screening settings may only contain "
			+ ", ".join(sorted(allowed_settings))
			+ "."
		)
	human_review_threshold = settings.get("human_review_confidence_threshold", "High")
	if not isinstance(human_review_threshold, str) or human_review_threshold not in {
		"Medium", "High"
	}:
		raise ValueError(
			"human_review_confidence_threshold must be 'Medium' or 'High'."
		)
	coverage_threshold = settings.get("urban_built_up_coverage_threshold_percent", 50)
	if (
		isinstance(coverage_threshold, bool)
		or not isinstance(coverage_threshold, (int, float))
		or not math.isfinite(coverage_threshold)
		or not 0 < coverage_threshold <= 100
	):
		raise ValueError(
			"urban_built_up_coverage_threshold_percent must be greater than 0 and at most 100."
		)
	return {
		"human_review_confidence_threshold": human_review_threshold,
		"urban_built_up_coverage_threshold_percent": float(coverage_threshold),
	}


def run_irz_code_spatial_screening(
	polygon: list[list[float]],
	context: dict[str, object],
	model_config_path: Union[str, Path],
	*,
	implementation_config_path: Optional[Union[str, Path]] = None,
) -> dict:
	"""Screen a BNG polygon using categories decoded from the IRZ link's codes."""
	implementation_config_path = (
		Path(implementation_config_path)
		if implementation_config_path is not None
		else _DEFAULT_IMPLEMENTATION_CONFIG
	)
	settings = _load_spatial_screening_settings(implementation_config_path)
	human_review_threshold = settings["human_review_confidence_threshold"]
	urban_threshold = settings["urban_built_up_coverage_threshold_percent"]
	print("[spatial screening] Starting urban/rural check.")
	built_up_result = intersect_polygon_with_layer(
		_BUILT_UP_AREAS_DATA_SOURCE,
		_BUILT_UP_AREAS_LAYER,
		polygon,
		polygon_crs="EPSG:27700",
	)
	built_up_coverage = built_up_result["intersection_percentage"]
	urban_rural_check = {
		"classification": "urban" if built_up_coverage >= urban_threshold else "rural",
		"built_up_area_coverage_percentage": built_up_coverage,
		"threshold_percentage": urban_threshold,
		"source_layer": _BUILT_UP_AREAS_LAYER,
	}
	print(
		f"[spatial screening] Completed urban/rural check: "
		f"{urban_rural_check['classification']} "
		f"({built_up_coverage:.2f}% built-up coverage)."
	)
	print("[spatial screening] Starting Best and Most Versatile Land check.")
	best_and_most_versatile_land_result = intersect_polygon_with_layer(
		_BEST_AND_MOST_VERSATILE_LAND_DATA_SOURCE,
		_BEST_AND_MOST_VERSATILE_LAND_LAYER,
		polygon,
		polygon_crs="EPSG:27700",
	)
	print(
		"[spatial screening] Completed Best and Most Versatile Land check: "
		f"intersects={best_and_most_versatile_land_result['intersects']}."
	)
	print("[spatial screening] Starting protected landscape check.")
	protected_landscape_result = intersect_polygon_with_layer(
		_PROTECTED_LANDSCAPE_DATA_SOURCE,
		_PROTECTED_LANDSCAPE_LAYER,
		polygon,
		polygon_crs="EPSG:27700",
	)
	print(
		"[spatial screening] Completed protected landscape check: "
		f"intersects={protected_landscape_result['intersects']}."
	)
	best_and_most_versatile_land_check = {
		"intersects": best_and_most_versatile_land_result["intersects"],
		"source_layer": _BEST_AND_MOST_VERSATILE_LAND_LAYER,
	}
	protected_landscape_check = {
		"intersects": protected_landscape_result["intersects"],
		"source_layer": _PROTECTED_LANDSCAPE_LAYER,
	}
	model_context = dict(context)
	spatial_context = model_context.get("spatial_screening", {})
	if not isinstance(spatial_context, dict):
		raise ValueError("context['spatial_screening'] must be a mapping when provided.")
	model_context["spatial_screening"] = {
		**spatial_context,
		"settlement_type": urban_rural_check["classification"],
	}
	print("[spatial screening] Starting internal IRZ link retrieval.")
	internal_link = InternalIRZClient(
		config_path=implementation_config_path
	).get_internal_link(polygon)
	print("[spatial screening] Completed internal IRZ link retrieval.")
	irz_codes = parse_internal_irz_codes(internal_link)
	categories = map_internal_irz_codes(irz_codes)
	strategic_solutions = check_strategic_irz_codes(irz_codes)
	print(
		f"[spatial screening] Completed IRZ code mapping: "
		f"{len(irz_codes)} records, {len(categories)} categories, "
		f"{len(strategic_solutions)} strategic solutions."
	)
	user_prompt = (
		"IRZ development categories and available options:\n"
		+ json.dumps(categories, ensure_ascii=False, indent=2)
		+ "\n\nConsultation context:\n"
		+ json.dumps(model_context, ensure_ascii=False, indent=2)
	)
	system_prompt = _ANSWER_IRZ_CATEGORY_PROMPT.read_text(encoding="utf-8")
	print("[spatial screening] Starting IRZ category answer generation.")
	with LLMClient(model_config_path) as intelligence:
		response = intelligence.generate(user_prompt, system_prompt=system_prompt)
		provider = intelligence.config.provider
		model = intelligence.config.model
	print("[spatial screening] Completed IRZ category answer generation.")

	print("[spatial screening] Raw IRZ model response:")
	print(response)
	print("[spatial screening] Starting IRZ answer validation.")
	answers = parse_irz_category_answers(response, categories)
	for answer in answers:
		answer["human_review_required"] = (
			_CONFIDENCE_LEVELS[answer["confidence"]]
			< _CONFIDENCE_LEVELS[human_review_threshold]
		)
	print(
		f"[spatial screening] Completed IRZ answer validation: "
		f"{len(answers)} answers, "
		f"human review required={any(answer['human_review_required'] for answer in answers)}."
	)

	return {
		"polygon": polygon,
		"internal_link": internal_link,
		"irz_codes": irz_codes,
		"categories": categories,
		"strategic_solutions": strategic_solutions,
		"is_strategic_consultation": bool(strategic_solutions),
		"geospatial_checks": {
			"urban_rural": urban_rural_check,
			"best_and_most_versatile_land": best_and_most_versatile_land_check,
			"protected_landscape_impact_risk_zones": protected_landscape_check,
		},
		"answers": answers,
		"human_review_confidence_threshold": human_review_threshold,
		"human_review_required": any(
			answer["human_review_required"] for answer in answers
		),
		"provider": provider,
		"model": model,
		"response": response,
	}