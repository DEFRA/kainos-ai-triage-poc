"""Small utilities for document-screening outputs."""

import json


_DOCUMENT_FINDING_KEYS = {
    "completed_assessments",
    "planning_permissions",
    "location",
    "other",
    "limitations",
}


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

    for key in ("completed_assessments", "planning_permissions", "other", "limitations"):
        if not isinstance(findings[key], list):
            raise ValueError(f"Model response field {key!r} must be a JSON array.")

    location = findings["location"]
    if not isinstance(location, dict) or set(location) != {"coordinates", "addresses"}:
        raise ValueError("Model response field 'location' must contain coordinates and addresses.")
    for key in ("coordinates", "addresses"):
        if not isinstance(location[key], list):
            raise ValueError(f"Model response field 'location.{key}' must be a JSON array.")

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