"""Small utilities for document-screening outputs."""

import math
import re
from collections import Counter
from pathlib import Path

import yaml

from backend.src.utilities.llm_json_parser import parse_llm_json


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


_PRIORITY_RANK = {"skip": 0, "maybe": 1, "must": 2}
_SEPARATOR_RUN = re.compile(r"([-_=.·•*~/\\|])\1{2,}")
_HEADER_FOOTER_LINES = 3


def clean_document_text(text: str) -> str:
    """Shorten separator runs and collapse spaces and blank lines; words are unchanged."""
    text = _SEPARATOR_RUN.sub(r"\1", text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    return re.sub(r" *\n[ \n]*", "\n", text).strip()


def clean_document_pages(page_texts: list[str]) -> list[str]:
    """Clean each page and drop repeated headers and footers.

    A line is treated as a header or footer only when it sits within the first
    or last few lines of at least half of the pages (documents of 3+ pages), so
    repeated body text such as form answers is kept.
    """
    pages = [clean_document_text(text).split("\n") for text in page_texts]
    if len(pages) < 3:
        return ["\n".join(lines) for lines in pages]
    edge_counts = Counter(
        line
        for lines in pages
        for line in set(lines[:_HEADER_FOOTER_LINES] + lines[-_HEADER_FOOTER_LINES:])
        if line
    )
    repeated = {line for line, count in edge_counts.items() if count >= len(pages) / 2}
    cleaned = []
    for lines in pages:
        edge_positions = set(range(min(_HEADER_FOOTER_LINES, len(lines)))) | set(
            range(max(len(lines) - _HEADER_FOOTER_LINES, 0), len(lines))
        )
        cleaned.append("\n".join(
            line for position, line in enumerate(lines)
            if not (position in edge_positions and line in repeated)
        ))
    return cleaned


def parse_prioritization(
    response: str, expected_ids: list[str],
) -> tuple[list[dict], list[dict]]:
    """Return one record per expected ID plus warnings about the model's ID handling.

    Unknown IDs are dropped, repeated IDs keep their highest priority and are
    marked uncertain, and missing IDs default to an uncertain maybe. Each case
    is reported as a warning rather than an error; malformed records still raise.
    """
    if not isinstance(response, str):
        raise TypeError("The prioritization response must be text.")

    try:
        result = parse_llm_json(response)
    except ValueError as error:
        raise ValueError(f"Prioritization response is not valid JSON: {error}") from error
    if not isinstance(result, dict) or set(result) != {"documents"}:
        raise ValueError("Prioritization response must contain only a 'documents' array.")
    records = result["documents"]
    if not isinstance(records, list):
        raise ValueError("Prioritization response 'documents' must be an array.")

    by_id = {}
    duplicate_ids = []
    unexpected_ids = []
    required_fields = {"id", "priority", "uncertain", "reason"}
    for record in records:
        if not isinstance(record, dict) or set(record) != required_fields:
            raise ValueError("Each prioritization record must contain id, priority, uncertain, and reason.")
        document_id = record["id"]
        # Numeric IDs are accepted because callers may send documents as numbers.
        if type(document_id) is int:
            document_id = str(document_id)
        if not isinstance(document_id, str) or not document_id.strip():
            raise ValueError("Prioritization document IDs must be non-empty strings.")
        document_id = document_id.strip()
        if record["priority"] not in _PRIORITY_RANK:
            raise ValueError(f"Invalid priority for document {document_id!r}.")
        if type(record["uncertain"]) is not bool:
            raise ValueError(f"uncertain must be a boolean for document {document_id!r}.")
        if not isinstance(record["reason"], str) or not record["reason"].strip():
            raise ValueError(f"A non-empty reason is required for document {document_id!r}.")
        if document_id not in expected_ids:
            if document_id not in unexpected_ids:
                unexpected_ids.append(document_id)
            continue
        parsed = {
            "id": document_id,
            "priority": record["priority"],
            "uncertain": record["uncertain"],
            "reason": record["reason"].strip(),
        }
        previous = by_id.get(document_id)
        if previous is not None:
            if document_id not in duplicate_ids:
                duplicate_ids.append(document_id)
            if _PRIORITY_RANK[parsed["priority"]] > _PRIORITY_RANK[previous["priority"]]:
                previous.update(priority=parsed["priority"], reason=parsed["reason"])
            previous["uncertain"] = True
            previous["duplicate_in_response"] = True
            continue
        by_id[document_id] = parsed

    missing_ids = [document_id for document_id in expected_ids if document_id not in by_id]
    for document_id in missing_ids:
        by_id[document_id] = {
            "id": document_id,
            "priority": "maybe",
            "uncertain": True,
            "reason": "The model returned no prioritization for this document; defaulted to maybe.",
            "missing_from_response": True,
        }

    warnings = []
    if missing_ids:
        warnings.append({
            "type": "missing_document_prioritizations",
            "document_ids": missing_ids,
            "message": "Missing model priorities defaulted to maybe; these documents were not selected unless include_maybe is true.",
        })
    if duplicate_ids:
        warnings.append({
            "type": "duplicate_document_prioritizations",
            "document_ids": duplicate_ids,
            "message": "The model prioritized these documents more than once; the highest priority was kept and marked uncertain.",
        })
    if unexpected_ids:
        warnings.append({
            "type": "unexpected_document_ids",
            "returned_ids": unexpected_ids,
            "message": "The model returned IDs that were not supplied; those records were ignored.",
        })
    return [by_id[document_id] for document_id in expected_ids], warnings


def _compile_rule_patterns(value: object, name: str) -> list[re.Pattern]:
    if value is None:
        return []
    if not isinstance(value, list) or any(
        not isinstance(pattern, str) or not pattern.strip() for pattern in value
    ):
        raise ValueError(f"{name} must be a list of non-empty regular expressions.")
    compiled = []
    for pattern in value:
        try:
            compiled.append(re.compile(pattern.strip(), re.IGNORECASE))
        except re.error as error:
            raise ValueError(f"{name} contains an invalid pattern {pattern!r}: {error}") from error
    return compiled


def _normalise_rule_text(value: object) -> str:
    """Lower-case text and reduce punctuation and repeated spaces to single spaces."""
    text = value if isinstance(value, str) else ""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.casefold()).split())


def match_document_priority_rule(
    document: dict, must_patterns: list[re.Pattern], skip_patterns: list[re.Pattern],
) -> dict | None:
    """Return a configured must or skip priority for a document, or None.

    Must patterns are checked against the normalised type and description and
    win over skip patterns, which are checked against the normalised type only.
    """
    normalised = {
        "type": _normalise_rule_text(document.get("type")),
        "description": _normalise_rule_text(document.get("description")),
    }
    for pattern in must_patterns:
        for field in ("type", "description"):
            if pattern.search(normalised[field]):
                return {
                    "priority": "must",
                    "reason": (
                        f"Must by document rule '{pattern.pattern}' "
                        f"({field}: '{document.get(field)}')."
                    ),
                }
    for pattern in skip_patterns:
        if pattern.search(normalised["type"]):
            return {
                "priority": "skip",
                "reason": (
                    f"Skipped by document type rule '{pattern.pattern}' "
                    f"(type: '{document.get('type')}')."
                ),
            }
    return None


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
        "include_attachments",
        "default_polygon_area_square_metres",
        "pre_screening_batch_size",
        "max_concurrent_pre_screening_calls",
        "max_pre_screening_batch_characters",
        "clean_document_text",
        "must_document_patterns",
        "skip_document_type_patterns",
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
    include_attachments = settings.get("include_attachments", True)
    if type(include_attachments) is not bool:
        raise ValueError("include_attachments must be true or false.")
    pre_screening_batch_size = settings.get("pre_screening_batch_size", 3)
    if type(pre_screening_batch_size) is not int or pre_screening_batch_size < 1:
        raise ValueError("pre_screening_batch_size must be a positive integer.")
    max_concurrent_pre_screening_calls = settings.get("max_concurrent_pre_screening_calls", 3)
    if type(max_concurrent_pre_screening_calls) is not int or max_concurrent_pre_screening_calls < 1:
        raise ValueError("max_concurrent_pre_screening_calls must be a positive integer.")
    max_pre_screening_batch_characters = settings.get("max_pre_screening_batch_characters", 150_000)
    if type(max_pre_screening_batch_characters) is not int or max_pre_screening_batch_characters < 1:
        raise ValueError("max_pre_screening_batch_characters must be a positive integer.")
    clean_text = settings.get("clean_document_text", True)
    if type(clean_text) is not bool:
        raise ValueError("clean_document_text must be true or false.")
    must_document_patterns = _compile_rule_patterns(
        settings.get("must_document_patterns"), "must_document_patterns",
    )
    skip_document_type_patterns = _compile_rule_patterns(
        settings.get("skip_document_type_patterns"), "skip_document_type_patterns",
    )

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
        "include_attachments": include_attachments,
        "default_polygon_area_square_metres": default_polygon_area_square_metres,
        "pre_screening_batch_size": pre_screening_batch_size,
        "max_concurrent_pre_screening_calls": max_concurrent_pre_screening_calls,
        "max_pre_screening_batch_characters": max_pre_screening_batch_characters,
        "clean_document_text": clean_text,
        "must_document_patterns": must_document_patterns,
        "skip_document_type_patterns": skip_document_type_patterns,
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
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    for key in ("value", "confidence"):
        field_value = value.get(key)
        if isinstance(field_value, str) and field_value.strip().casefold() == "null":
            value[key] = None


def _validate_signal(value: object, label: str) -> None:
    signal = _require_mapping(
        value, {"classification", "confidence", "reason", "evidence"}, label
    )
    if not isinstance(signal["classification"], str) or signal["classification"] not in {"yes", "no_signal"}:
        raise ValueError(f"{label} has an invalid classification.")
    if isinstance(signal["confidence"], str) and signal["confidence"].strip().casefold() == "null":
        signal["confidence"] = None
    # Decision ignores unknown confidences, so a missing one is tolerated only
    # when nothing was found; a positive signal must state its confidence.
    missing_confidence_allowed = (
        signal["confidence"] is None and signal["classification"] == "no_signal"
    )
    if not missing_confidence_allowed and (
        not isinstance(signal["confidence"], str) or signal["confidence"] not in _CONFIDENCE_LEVELS
    ):
        raise ValueError(f"{label} has an invalid confidence.")
    if not isinstance(signal["reason"], str):
        raise ValueError(f"{label} reason must be a string.")
    _validate_evidence(signal["evidence"], f"{label} evidence")
    if (signal["classification"] == "no_signal") != (not signal["evidence"]):
        raise ValueError(f"{label} evidence must match its classification.")


def parse_document_findings(response: str) -> dict[str, object]:
    """Parse and validate the prompt's JSON object, repairing it if needed."""
    if not isinstance(response, str):
        raise TypeError("The model response must be text.")

    try:
        findings = parse_llm_json(response)
    except ValueError as error:
        raise ValueError(f"Model response is not valid JSON: {error}") from error

    if not isinstance(findings, dict):
        raise ValueError("Model response must be a JSON object.")

    signals = findings.get("consultation_type_signals")
    decision_signals = ("amendment", "minerals_waste_existing", "minerals_waste_new")
    if not isinstance(signals, dict) or any(name not in signals for name in decision_signals):
        raise ValueError("Model response has an invalid top-level structure or missing decision signals.")
    for name in decision_signals:
        _validate_signal(signals[name], f"consultation_type_signals.{name}")

    incomplete_fields = sorted(_DOCUMENT_FINDING_KEYS - findings.keys())
    location = findings.get("location")
    coordinates = location.get("coordinates") if isinstance(location, dict) else None
    if isinstance(coordinates, dict):
        for key in ("easting", "northing"):
            coordinate = coordinates.get(key)
            if isinstance(coordinate, str):
                try:
                    coordinate = float(coordinate.strip())
                except ValueError:
                    continue
                if math.isfinite(coordinate):
                    coordinates[key] = int(coordinate) if coordinate.is_integer() else coordinate
    for section_name in ("location", "site_and_surroundings", "agricultural_land", "scale_and_type_of_development"):
        section = findings.get(section_name)
        if isinstance(section, dict):
            for name, field in section.items():
                if isinstance(field, dict) and name != "coordinates":
                    _validate_field(field, f"{section_name}.{name}")
                    evidence = field.get("evidence")
                    if isinstance(evidence, list) and any(
                        not isinstance(item, dict) or not isinstance(item.get("document_id"), str)
                        for item in evidence
                    ):
                        incomplete_fields.append(f"{section_name}.{name}.evidence.document_id")

    development = findings.get("scale_and_type_of_development")
    incomplete_development = []
    if not isinstance(development, dict):
        incomplete_development.append("scale_and_type_of_development")
    else:
        for name in sorted(_SCALE_AND_TYPE_KEYS):
            field = development.get(name)
            if (
                not isinstance(field, dict)
                or "value" not in field
                or field.get("status") not in ("stated", "not_stated")
                or (field.get("status") == "stated" and (
                    not isinstance(field.get("value"), str) or not field["value"].strip()
                ))
                or (field.get("status") == "not_stated" and field.get("value") is not None)
            ):
                incomplete_development.append(f"scale_and_type_of_development.{name}")
    if incomplete_fields or incomplete_development:
        findings["screening_completeness"] = {
            "complete": False,
            "missing_fields": sorted(set(incomplete_fields + incomplete_development)),
            "incomplete_development_fields": incomplete_development,
        }

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
        if (
            coordinates.get("status") != "stated"
            or coordinates.get("confidence") not in ("medium", "high")
        ):
            raise ValueError(
                "Cannot infer polygon: coordinates must be stated with medium or high confidence."
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