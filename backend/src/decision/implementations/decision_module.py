"""Apply the consultation checklist and categorical confidence rules."""

import math


def _or_confidence(outcomes: list[tuple[bool, str | None]]) -> str | None:
    """Use the strongest positive support, or the weakest of all negatives."""
    confidence_order = {"low": 0, "medium": 1, "high": 2}
    if any(answer for answer, confidence in outcomes):
        positive_confidences = [
            confidence for answer, confidence in outcomes
            if answer and confidence is not None
        ]
        return max(positive_confidences, key=confidence_order.__getitem__, default=None)
    confidences = [confidence for answer, confidence in outcomes]
    if not confidences or None in confidences:
        return None
    return min(confidences, key=confidence_order.__getitem__)


def _checklist_item(guidance_id: str, findings: list[tuple[str, dict, bool]]) -> dict:
    reasons = []
    evidence = []
    outcomes = []
    for stage, finding, answer in findings:
        reason = finding.get("reason", "")
        confidence = finding.get("confidence")
        if not isinstance(reason, str):
            raise ValueError(f"{guidance_id}: reason must be text.")
        if confidence not in (None, "high", "medium", "low"):
            raise ValueError(f"{guidance_id}: invalid confidence.")
        if reason:
            reasons.append(f"{stage}: {reason}")
        outcomes.append((answer, confidence))
        items = finding.get("evidence", [])
        if not isinstance(items, list):
            raise ValueError(f"{guidance_id}: evidence must be a list.")
        for item in items:
            if not isinstance(item, dict) or any(
                not isinstance(item.get(field), str) for field in ("source", "quote")
            ):
                raise ValueError(f"{guidance_id}: evidence needs source and quote strings.")
            evidence.append({"stage": stage, "source": item["source"], "quote": item["quote"]})
    return {
        "guidance_id": guidance_id,
        "answer": any(answer for answer, confidence in outcomes),
        "reason": "\n".join(reasons),
        "evidence": evidence,
        "confidence": _or_confidence(outcomes),
    }


def run_decision(stage_outputs: dict) -> dict:
    """Evaluate 1b-1k; IRZ category coverage must be validated upstream.

    Signals are direct stage children with classification/reason/evidence/confidence.
    OR rules use the highest known confidence among positive inputs, or the lowest
    among all negative inputs. An unknown negative confidence prevents an all-no
    confidence; unknown positives do not weaken another known positive. The same
    rules apply to overall confidence. Land confidence is supplied upstream for
    the combined spatial finding, not inferred from its measurements.
    """
    if not isinstance(stage_outputs, dict):
        raise ValueError("Decision input must be a mapping of stage outputs.")
    for stage in ("case_intake", "document_screening", "spatial_screening"):
        if not isinstance(stage_outputs.get(stage), dict):
            raise ValueError(f"{stage} must be a mapping.")

    checklist = []
    signal_rules = (
        ("1b", ("eia_screening", "eia_scoping"), ("case_intake",)),
        ("1c", ("amendment",), ("case_intake",)),
        ("1d", ("conditions",), ("case_intake",)),
        ("1e", ("pre_application",), ("case_intake",)),
        ("1f", ("prior_approval",), ("case_intake",)),
        ("1g", ("minerals_waste_existing",), ("case_intake", "document_screening")),
        ("1h", ("minerals_waste_new",), ("case_intake", "document_screening")),
    )
    for guidance_id, fields, stages in signal_rules:
        findings = []
        for stage, field in ((stage, field) for stage in stages for field in fields):
            finding = stage_outputs[stage].get(field)
            if not isinstance(finding, dict) or finding.get("classification") not in (
                "yes", "no_signal"
            ):
                raise ValueError(f"{stage}.{field} requires yes or no_signal classification.")
            findings.append((stage, finding, finding["classification"] == "yes"))
        checklist_item = _checklist_item(guidance_id, findings)
        checklist.append(checklist_item)

    spatial = stage_outputs["spatial_screening"]
    land = spatial.get("best_and_most_versatile_land")
    if not isinstance(land, dict) or type(land.get("intersects")) is not bool:
        raise ValueError("best_and_most_versatile_land requires a boolean intersects.")
    area = land.get("overlap_area_hectares")
    if (
        isinstance(area, bool) or not isinstance(area, (int, float))
        or not math.isfinite(area) or area < 0
    ):
        raise ValueError("overlap_area_hectares must be a finite non-negative number.")
    land_finding = {
        **land,
        "reason": land.get("reason") or (
            f"BMV intersects={land['intersects']}; overlap={area} hectares; "
            "the rule requires intersection and overlap greater than 20 hectares."
        ),
    }
    checklist.append(_checklist_item(
        "1i", [("spatial_screening", land_finding, land["intersects"] and area > 20)]
    ))

    answers = spatial.get("answers")
    if not isinstance(answers, list) or not answers:
        raise ValueError("Spatial answers must be a non-empty, complete list.")
    categories = set()
    irz_findings = []
    all_consultations = None
    for answer in answers:
        if not isinstance(answer, dict):
            raise ValueError("Each IRZ answer must be a mapping.")
        category = answer.get("category")
        value = answer.get("value")
        if not isinstance(category, str) or not category.strip() or category in categories:
            raise ValueError("IRZ answers require unique, non-empty category names.")
        if type(value) is not int or value < 0:
            raise ValueError(f"IRZ answer {category!r} requires a non-negative integer value.")
        categories.add(category)
        confidence = answer.get("confidence")
        if confidence is not None and not isinstance(confidence, str):
            raise ValueError(f"IRZ answer {category!r} has invalid confidence.")
        evidence = answer.get("supporting_evidence", [])
        if not isinstance(evidence, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("source"), str)
            or not isinstance(item.get("evidence"), str)
            for item in evidence
        ):
            raise ValueError(f"IRZ answer {category!r} has invalid supporting_evidence.")
        finding = {
            "reason": answer.get("reasoning", ""),
            "confidence": confidence.lower() if confidence is not None else None,
            "evidence": [
                {"source": item["source"], "quote": item["evidence"]} for item in evidence
            ],
        }
        irz_findings.append(("spatial_screening", finding, value > 0))
        if category == "All Consultations":
            all_consultations = (value, finding)
    if all_consultations is None:
        raise ValueError("IRZ answers must include All Consultations for checklist item 1j.")
    value, finding = all_consultations
    checklist.append(_checklist_item("1j", [("spatial_screening", finding, value == 1)]))
    checklist.append(_checklist_item("1k", irz_findings))
    return {
        "classification": "necessary" if any(item["answer"] for item in checklist) else "unnecessary",
        "confidence": _or_confidence([(item["answer"], item["confidence"]) for item in checklist]),
        "checklist": checklist,
    }