"""Small utilities for document-screening outputs."""

import json
import math
import re
from pathlib import Path

import yaml


_DOCUMENT_FINDING_KEYS = {
    "consultation_type_signals",
    "location",
    "site_and_surroundings",
    "scale_and_type_of_development",
    "agricultural_land",
    "assessments",
    "limitations",
    "notes",
}
_CONSULTATION_SIGNAL_KEYS = {
    "eia_screening",
    "eia_scoping",
    "amendment",
    "conditions",
    "pre_application",
    "prior_approval",
    "minerals_waste_existing",
    "minerals_waste_new",
}
_SCALE_AND_TYPE_KEYS = {
    "all_consultations",
    "infrastructure",
    "wind_solar",
    "minerals_oil_gas",
    "rural_non_residential",
    "residential",
    "rural_residential",
    "air_pollution",
    "combustion",
    "waste",
    "compost",
    "discharge",
    "water_supply",
}
_FIELD_KEYS = {"value", "status", "confidence", "reason", "evidence"}
_EVIDENCE_KEYS = {"document_id", "page", "quote"}
_CONFIDENCE_LEVELS = {"high", "medium", "low"}
_SITE_AREA_PATTERN = re.compile(
    r"^\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*"
    r"(m²|m2|sqm|sq\.?\s*m(?:etres?)?|square\s+metres?|ha|hectares?|acres?)[.]?\s*$",
    re.IGNORECASE,
)
_AREA_TO_SQUARE_METRES = {
    "m²": 1.0,
    "m2": 1.0,
    "sqm": 1.0,
    "sq m": 1.0,
    "sq metre": 1.0,
    "sq metres": 1.0,
    "square metre": 1.0,
    "square metres": 1.0,
    "ha": 10_000.0,
    "hectare": 10_000.0,
    "hectares": 10_000.0,
    "acre": 4_046.8564224,
    "acres": 4_046.8564224,
}
DEFAULT_POLYGON_AREA_SQUARE_METRES = 50_000.0
_DEFAULT_DOCUMENT_SCREENING_CONFIG = (
    Path(__file__).resolve().parents[4]
    / "environment"
    / "configs"
    / "document_screening.yaml"
)


def parse_prioritization(response: str, expected_ids: list[str]) -> list[dict]:
    if not isinstance(response, str):
        raise TypeError("The prioritization response must be text.")

    content = response.strip()
    if content.startswith("```") and content.endswith("```"):
        lines = content.splitlines()
        if len(lines) >= 3 and lines[0].strip().casefold() in {"```", "```json"} and lines[-1].strip() == "```":
            content = "\n".join(lines[1:-1]).strip()

    try:
        result = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Prioritization response is not valid JSON (line {error.lineno}, "
            f"column {error.colno}): {error.msg}."
        ) from error
    if not isinstance(result, dict) or set(result) != {"documents"}:
        raise ValueError("Prioritization response must contain only a 'documents' array.")
    records = result["documents"]
    if not isinstance(records, list):
        raise ValueError("Prioritization response 'documents' must be an array.")

    by_id = {}
    required_fields = {"id", "priority", "uncertain", "reason"}
    for record in records:
        if not isinstance(record, dict) or set(record) != required_fields:
            raise ValueError("Each prioritization record must contain id, priority, uncertain, and reason.")
        document_id = record["id"]
        if not isinstance(document_id, str) or not document_id.strip():
            raise ValueError("Prioritization document IDs must be non-empty strings.")
        if document_id in by_id:
            raise ValueError(f"Prioritization response repeats document ID {document_id!r}.")
        if record["priority"] not in {"must", "maybe", "skip"}:
            raise ValueError(f"Invalid priority for document {document_id!r}.")
        if type(record["uncertain"]) is not bool:
            raise ValueError(f"uncertain must be a boolean for document {document_id!r}.")
        if not isinstance(record["reason"], str) or not record["reason"].strip():
            raise ValueError(f"A non-empty reason is required for document {document_id!r}.")
        by_id[document_id] = {
            "id": document_id,
            "priority": "must" if record["uncertain"] else record["priority"],
            "uncertain": record["uncertain"],
            "reason": record["reason"].strip(),
        }

    if set(by_id) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(by_id))
        unexpected = sorted(set(by_id) - set(expected_ids))
        details = []
        if missing:
            details.append("missing IDs: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected IDs: " + ", ".join(unexpected))
        raise ValueError("Prioritization response does not match the metadata (" + "; ".join(details) + ").")
    return [by_id[document_id] for document_id in expected_ids]


def _validate_default_polygon_area(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError("default_polygon_area_square_metres must be a positive finite number.")
    return float(value)


def load_prioritization_config(config_path: str | Path | None = None) -> dict:
    config_path = Path(config_path) if config_path is not None else _DEFAULT_DOCUMENT_SCREENING_CONFIG
    try:
        with config_path.open(encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid implementation YAML in {config_path}: {error}") from error

    if not isinstance(config, dict) or set(config) != {"document_screening", "lpa"}:
        raise ValueError(
            f"Implementation config {config_path} must contain 'document_screening' and 'lpa' mappings."
        )
    settings = config["document_screening"]
    allowed = {
        "max_concurrent_downloads",
        "include_maybe",
        "default_polygon_area_square_metres",
    }
    if not isinstance(settings, dict) or settings.keys() - allowed:
        raise ValueError(
            f"Implementation settings must be a mapping with only: {', '.join(sorted(allowed))}."
        )

    max_concurrent_downloads = settings.get("max_concurrent_downloads", 3)
    if type(max_concurrent_downloads) is not int or max_concurrent_downloads < 1:
        raise ValueError("max_concurrent_downloads must be a positive integer.")

    include_maybe = settings.get("include_maybe", False)
    if type(include_maybe) is not bool:
        raise ValueError("include_maybe must be true or false.")

    default_polygon_area_square_metres = _validate_default_polygon_area(
        settings.get("default_polygon_area_square_metres", DEFAULT_POLYGON_AREA_SQUARE_METRES)
    )
    lpa_settings = config["lpa"]
    lpa_allowed = {
        "request_interval_seconds", "retry_increment_seconds", "max_retries",
        "max_wait_seconds", "refresh_session_on_429", "session_refresh_wait_seconds",
        "max_session_refreshes",
    }
    if not isinstance(lpa_settings, dict) or lpa_settings.keys() - lpa_allowed:
        raise ValueError(
            f"LPA settings must be a mapping with only: {', '.join(sorted(lpa_allowed))}."
        )

    return {
        "max_concurrent_downloads": max_concurrent_downloads,
        "include_maybe": include_maybe,
        "default_polygon_area_square_metres": default_polygon_area_square_metres,
        "lpa_settings": lpa_settings,
    }


def load_default_polygon_area_square_metres(
    config_path: str | Path | None = None,
) -> float:
    config_path = Path(config_path) if config_path is not None else _DEFAULT_DOCUMENT_SCREENING_CONFIG
    try:
        with config_path.open(encoding="utf-8") as config_file:
            config = yaml.safe_load(config_file)
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid document-screening YAML in {config_path}: {error}") from error

    if not isinstance(config, dict):
        raise ValueError(f"Document-screening config {config_path} must be a YAML mapping.")
    settings = config.get("document_screening")
    if not isinstance(settings, dict):
        raise ValueError("'document_screening' config section must be a mapping.")
    area = settings.get(
        "default_polygon_area_square_metres",
        DEFAULT_POLYGON_AREA_SQUARE_METRES,
    )
    return _validate_default_polygon_area(area)


def _require_mapping(value: object, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or value.keys() != keys:
        raise ValueError(f"{label} must contain exactly: {', '.join(sorted(keys))}.")
    return value


def _validate_evidence(value: object, label: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array.")
    for item in value:
        evidence = _require_mapping(item, _EVIDENCE_KEYS, f"Each {label} item")
        if not isinstance(evidence["document_id"], str):
            raise ValueError(f"{label} document_id must be a string.")
        if evidence["page"] is not None and type(evidence["page"]) is not int:
            raise ValueError(f"{label} page must be an integer or null.")
        if not isinstance(evidence["quote"], str):
            raise ValueError(f"{label} quote must be a string.")


def _validate_field(value: object, label: str) -> None:
    field = _require_mapping(value, _FIELD_KEYS, label)
    if field["value"] is not None and not isinstance(field["value"], str):
        raise ValueError(f"{label} value must be a string or null.")
    if not isinstance(field["status"], str) or field["status"] not in {
        "stated", "not_stated", "conflicting", "unclear"
    }:
        raise ValueError(f"{label} has an invalid status.")
    if field["confidence"] is not None and (
        not isinstance(field["confidence"], str)
        or field["confidence"] not in _CONFIDENCE_LEVELS
    ):
        raise ValueError(f"{label} has an invalid confidence.")
    if not isinstance(field["reason"], str):
        raise ValueError(f"{label} reason must be a string.")
    _validate_evidence(field["evidence"], f"{label} evidence")


def _validate_signal(value: object, label: str) -> None:
    signal = _require_mapping(
        value, {"classification", "confidence", "reason", "evidence"}, label
    )
    if not isinstance(signal["classification"], str) or signal["classification"] not in {"yes", "no_signal"}:
        raise ValueError(f"{label} has an invalid classification.")
    if not isinstance(signal["confidence"], str) or signal["confidence"] not in _CONFIDENCE_LEVELS:
        raise ValueError(f"{label} has an invalid confidence.")
    if not isinstance(signal["reason"], str):
        raise ValueError(f"{label} reason must be a string.")
    _validate_evidence(signal["evidence"], f"{label} evidence")
    if (signal["classification"] == "no_signal") != (not signal["evidence"]):
        raise ValueError(f"{label} evidence must match its classification.")


def parse_document_findings(response: str) -> dict[str, object]:
    """Parse the prompt's JSON object, allowing one enclosing JSON code fence."""
    if not isinstance(response, str):
        raise TypeError("The model response must be text.")

    content = response.strip()
    if content.startswith("```") and content.endswith("```"):
        lines = content.splitlines()
        if (
            len(lines) >= 3
            and lines[0].strip().casefold() in {"```", "```json"}
            and lines[-1].strip() == "```"
        ):
            content = "\n".join(lines[1:-1]).strip()

    try:
        findings = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(
            "Model response is not valid JSON "
            f"(line {error.lineno}, column {error.colno}): {error.msg}."
        ) from error

    if not isinstance(findings, dict):
        raise ValueError("Model response must be a JSON object.")

    missing_keys = _DOCUMENT_FINDING_KEYS - findings.keys()
    unexpected_keys = findings.keys() - _DOCUMENT_FINDING_KEYS
    if missing_keys or unexpected_keys:
        details = []
        if missing_keys:
            details.append("missing keys: " + ", ".join(sorted(missing_keys)))
        if unexpected_keys:
            details.append("unexpected keys: " + ", ".join(sorted(unexpected_keys)))
        raise ValueError("Model response has an invalid top-level structure (" + "; ".join(details) + ").")

    signals = _require_mapping(
        findings["consultation_type_signals"],
        _CONSULTATION_SIGNAL_KEYS,
        "consultation_type_signals",
    )
    for name, signal in signals.items():
        _validate_signal(signal, f"consultation_type_signals.{name}")

    location = _require_mapping(
        findings["location"],
        {"coordinates", "site_address", "site_area"},
        "location",
    )
    coordinates = _require_mapping(
        location["coordinates"],
        {"easting", "northing", "grid_reference", "units", "crs", "confidence", "evidence"},
        "location.coordinates",
    )
    for key in ("easting", "northing"):
        coordinate = coordinates[key]
        if isinstance(coordinate, str):
            try:
                coordinate = float(coordinate.strip())
            except ValueError:
                raise ValueError(
                    f"location.coordinates.{key} must be a finite number or null."
                ) from None
            if coordinate.is_integer():
                coordinate = int(coordinate)
            coordinates[key] = coordinate
        if coordinate is not None and (
            type(coordinate) not in {int, float} or not math.isfinite(coordinate)
        ):
            raise ValueError(f"location.coordinates.{key} must be a finite number or null.")
    for key in ("grid_reference", "units", "crs"):
        if coordinates[key] is not None and not isinstance(coordinates[key], str):
            raise ValueError(f"location.coordinates.{key} must be a string or null.")
    if coordinates["confidence"] not in _CONFIDENCE_LEVELS:
        raise ValueError("location.coordinates has an invalid confidence.")
    _validate_evidence(coordinates["evidence"], "location.coordinates evidence")
    _validate_field(location["site_address"], "location.site_address")
    _validate_field(location["site_area"], "location.site_area")

    surroundings = _require_mapping(
        findings["site_and_surroundings"],
        {"existing_use", "greenfield_or_brownfield", "features_affected", "designated_sites_mentioned"},
        "site_and_surroundings",
    )
    for name in ("existing_use", "greenfield_or_brownfield"):
        _validate_field(surroundings[name], f"site_and_surroundings.{name}")
    for name in ("features_affected", "designated_sites_mentioned"):
        if not isinstance(surroundings[name], list):
            raise ValueError(f"site_and_surroundings.{name} must be an array.")
    for feature in surroundings["features_affected"]:
        feature = _require_mapping(
            feature, {"feature", "effect", "confidence", "evidence"},
            "Each features_affected item",
        )
        if not isinstance(feature["feature"], str):
            raise ValueError("features_affected feature must be a string.")
        if feature["effect"] not in {"loss", "fragmentation", "change_of_use", "retained", "not_stated"}:
            raise ValueError("features_affected has an invalid effect.")
        if feature["confidence"] not in _CONFIDENCE_LEVELS:
            raise ValueError("features_affected has an invalid confidence.")
        _validate_evidence(feature["evidence"], "features_affected evidence")
    for site in surroundings["designated_sites_mentioned"]:
        site = _require_mapping(
            site, {"name", "type", "distance", "confidence", "evidence"},
            "Each designated_sites_mentioned item",
        )
        for key in ("name", "type"):
            if not isinstance(site[key], str):
                raise ValueError(f"designated_sites_mentioned {key} must be a string.")
        if site["distance"] is not None and not isinstance(site["distance"], str):
            raise ValueError("designated_sites_mentioned distance must be a string or null.")
        if site["confidence"] not in _CONFIDENCE_LEVELS:
            raise ValueError("designated_sites_mentioned has an invalid confidence.")
        _validate_evidence(site["evidence"], "designated_sites_mentioned evidence")

    development = _require_mapping(
        findings["scale_and_type_of_development"],
        _SCALE_AND_TYPE_KEYS,
        "scale_and_type_of_development",
    )
    for name, field in development.items():
        _validate_field(field, f"scale_and_type_of_development.{name}")

    agricultural_land = _require_mapping(
        findings["agricultural_land"],
        {"agriculture", "agricultural_land_area_lost", "alc_grades", "soil_survey"},
        "agricultural_land",
    )
    for name, field in agricultural_land.items():
        _validate_field(field, f"agricultural_land.{name}")

    if not isinstance(findings["assessments"], list):
        raise ValueError("assessments must be an array.")
    for assessment in findings["assessments"]:
        _validate_field(assessment, "Each assessments item")
    if not isinstance(findings["limitations"], list):
        raise ValueError("limitations must be an array.")
    for limitation in findings["limitations"]:
        limitation = _require_mapping(
            limitation, {"document_id", "page", "issue"}, "Each limitations item"
        )
        if limitation["document_id"] is not None and not isinstance(limitation["document_id"], str):
            raise ValueError("limitations document_id must be a string or null.")
        if limitation["page"] is not None and type(limitation["page"]) is not int:
            raise ValueError("limitations page must be an integer or null.")
        if not isinstance(limitation["issue"], str):
            raise ValueError("limitations issue must be a string.")
    if not isinstance(findings["notes"], list) or any(
        not isinstance(note, str) for note in findings["notes"]
    ):
        raise ValueError("notes must be an array of strings.")

    return findings


def merge_document_findings(
    lpa_metadata: dict[str, object],
    document_findings: dict[str, object],
) -> dict[str, object]:
    """Combine LPA metadata with document findings without overwriting fields."""
    if not isinstance(lpa_metadata, dict) or not isinstance(document_findings, dict):
        raise TypeError("LPA metadata and document findings must both be JSON objects.")

    overlapping_keys = lpa_metadata.keys() & document_findings.keys()
    if overlapping_keys:
        keys = ", ".join(sorted(overlapping_keys))
        raise ValueError(f"Cannot merge overlapping top-level keys: {keys}.")

    return {**lpa_metadata, **document_findings}


def add_polygon_metadata(
    final_output: dict,
    default_polygon_area_square_metres: float = DEFAULT_POLYGON_AREA_SQUARE_METRES,
) -> dict:
    """Annotate an LPA polygon or infer a square from document coordinates and area."""
    polygon_data = final_output.get("polygon")
    existing_metadata = {}
    if isinstance(polygon_data, dict):
        polygon_rings = polygon_data.get("polygon")
        existing_metadata = {
            key: value for key, value in polygon_data.items() if key != "polygon"
        }
    else:
        polygon_rings = polygon_data

    if polygon_rings:
        if not isinstance(polygon_rings, list) or len(polygon_rings) != 1:
            raise ValueError("Document screening must provide exactly one BNG polygon ring.")
        ring = polygon_rings[0]
        if not isinstance(ring, list) or not ring:
            raise ValueError("Document screening must provide a non-empty BNG polygon ring.")
        metadata = {
            "polygon_source": "lpa_maps",
            "polygon_confidence": "high",
            "polygon_area_source": "lpa_polygon",
            **existing_metadata,
        }
    else:
        if polygon_rings is not None and not isinstance(polygon_rings, list):
            raise ValueError("Document screening polygon must contain a coordinate ring list.")
        location = final_output.get("location")
        if not isinstance(location, dict):
            raise ValueError(
                "Cannot infer polygon: no LPA polygon or document location is available."
            )
        coordinates = location.get("coordinates")
        if not isinstance(coordinates, dict):
            raise ValueError("Cannot infer polygon: document coordinates are unavailable.")
        if coordinates.get("confidence") not in {"medium", "high"}:
            raise ValueError(
                "Cannot infer polygon: coordinates must have medium or high confidence."
            )

        easting = coordinates.get("easting")
        northing = coordinates.get("northing")
        if (
            type(easting) not in {int, float}
            or type(northing) not in {int, float}
            or not math.isfinite(easting)
            or not math.isfinite(northing)
            or not (0 <= easting <= 700_000 and 0 <= northing <= 1_300_000)
        ):
            raise ValueError(
                "Cannot infer polygon: valid British National Grid easting and northing are required."
            )

        site_area = location.get("site_area")
        area_is_stated = (
            isinstance(site_area, dict)
            and site_area.get("status") == "stated"
            and site_area.get("confidence") in {"medium", "high"}
        )
        if area_is_stated:
            area_value = site_area.get("value")
            if not isinstance(area_value, str):
                raise ValueError("Cannot infer polygon: stated site area must include a value and unit.")
            area_match = _SITE_AREA_PATTERN.fullmatch(area_value)
            if area_match is None:
                raise ValueError(
                    "Cannot infer polygon: stated site area must be a single value with a supported unit."
                )
            area_square_metres = float(area_match.group(1).replace(",", ""))
            unit = re.sub(
                r"\s+", " ", area_match.group(2).strip().lower().replace(".", "")
            )
            area_square_metres *= _AREA_TO_SQUARE_METRES[unit]
            if not math.isfinite(area_square_metres) or area_square_metres <= 0:
                raise ValueError("Cannot infer polygon: site area must be positive and finite.")
            area_source = "document_site_area"
            polygon_confidence = "medium"
        else:
            area_square_metres = _validate_default_polygon_area(
                default_polygon_area_square_metres
            )
            area_source = "default_5_hectares"
            polygon_confidence = "low"

        half_side = math.sqrt(area_square_metres) / 2
        min_easting = easting - half_side
        max_easting = easting + half_side
        min_northing = northing - half_side
        max_northing = northing + half_side
        if not (
            0 <= min_easting <= max_easting <= 700_000
            and 0 <= min_northing <= max_northing <= 1_300_000
        ):
            raise ValueError("Cannot infer polygon: the square falls outside the British National Grid extent.")
        ring = [
            [min_easting, min_northing],
            [max_easting, min_northing],
            [max_easting, max_northing],
            [min_easting, max_northing],
            [min_easting, min_northing],
        ]
        metadata = {
            "polygon_source": "document_coordinates",
            "polygon_confidence": polygon_confidence,
            "polygon_area_source": area_source,
        }

    final_output["polygon"] = {"polygon": [ring], **metadata}
    return final_output