import csv
from bs4 import BeautifulSoup
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

import requests

_IRZ_CODE_TABLE_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "irz_code_table.csv"
)
_STRATEGIC_SOLUTION_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "ss_paragraphs.jsonl"
)


def parse_internal_irz_codes(internal_link):
    """Parse validated IRZ records from the internal link's ``irzcode`` query value."""
    if not isinstance(internal_link, str):
        raise TypeError("internal_link must be a URL string.")
    parsed_url = urlsplit(internal_link)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ValueError("internal_link must be an absolute HTTP(S) URL.")

    code_values = parse_qs(parsed_url.query, keep_blank_values=True).get("irzcode")
    if not code_values:
        raise ValueError("Internal IRZ URL does not contain an 'irzcode' parameter.")

    records = []
    for code_value in code_values:
        entries = code_value.split(";")
        while entries and not entries[-1].strip():
            entries.pop()
        if not entries:
            raise ValueError("The 'irzcode' parameter contains no records.")

        for entry in entries:
            fields = [field.strip() for field in entry.split(",")]
            if len(fields) != 4:
                raise ValueError(
                    "Each IRZ code record must contain four comma-separated fields."
                )
            site_id, irz_code, zone_selector, advice_reference = fields
            if not re.fullmatch(r"[0-6]{13}", irz_code):
                raise ValueError(
                    "Each IRZ development code must contain exactly 13 digits from 0 to 6."
                )
            records.append({
                "site_or_designation_id": site_id,
                "irz_code": irz_code,
                "irz_zone_selector": zone_selector,
                "advice_reference": advice_reference or None,
            })

    if not records:
        raise ValueError("The 'irzcode' parameter contains no records.")
    return records


def map_internal_irz_codes(irz_codes, code_table_path=None):
    """Map the union of positive IRZ code digits to available category options."""
    if not isinstance(irz_codes, list) or not irz_codes:
        raise ValueError("irz_codes must be a non-empty list of parsed records.")

    for record in irz_codes:
        code = record.get("irz_code") if isinstance(record, dict) else None
        if not isinstance(code, str) or not re.fullmatch(r"[0-6]{13}", code):
            raise ValueError("Each parsed IRZ record must contain a valid 13-digit irz_code.")

    code_table_path = (
        Path(code_table_path) if code_table_path is not None else _IRZ_CODE_TABLE_PATH
    )
    with code_table_path.open(encoding="utf-8-sig", newline="") as table_file:
        rows = list(csv.DictReader(table_file))

    table = {}
    for row in rows:
        try:
            digit = int(row["Digit"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("IRZ code table contains an invalid category digit.") from error
        category_name = row.get("Development type", "").strip()
        if digit in table or not 1 <= digit <= 13 or not category_name:
            raise ValueError("IRZ code table contains an invalid or duplicate category.")
        table[digit] = {
            "name": category_name,
            "descriptions": {
                option_value: (row.get(option_value) or "").strip()
                for option_value in ("1", "2", "3", "4", "5", "6")
            },
        }

    if set(table) != set(range(1, 14)):
        raise ValueError("IRZ code table must define category digits 1 through 13.")

    categories = []
    for category_digit in range(1, 14):
        category = table[category_digit]
        option_values = sorted({
            int(record["irz_code"][category_digit - 1])
            for record in irz_codes
            if record["irz_code"][category_digit - 1] != "0"
        })
        options = []
        for option_value in option_values:
            description = category["descriptions"][str(option_value)]
            if not description:
                raise ValueError(
                    f"IRZ code selects missing option {option_value} for "
                    f"{category['name']!r}."
                )
            options.append({"value": option_value, "text": description})
        options.append({"value": 0, "text": "Not applicable"})
        categories.append({"name": category["name"], "options": options})

    return categories


def check_strategic_irz_codes(irz_codes):
    """Match advice references from all-zero IRZ records to strategic paragraphs."""
    advice_references = {
        record.get("advice_reference")
        for record in irz_codes
        if record.get("irz_code") == "0" * 13 and record.get("advice_reference")
    }
    if not advice_references:
        return []

    with _STRATEGIC_SOLUTION_PATH.open(encoding="utf-8") as data_file:
        data = [json.loads(line) for line in data_file]
    return [
        record for record in data
        if str(record.get("gis_ref")) in advice_references
    ]


def parse_irz_category_answers(response, categories):
    """Parse and validate the model's selected value and text for every category."""
    if not isinstance(response, str):
        raise TypeError("The IRZ category response must be text.")

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
        result = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(
            "IRZ category response is not valid JSON "
            f"(line {error.lineno}, column {error.colno}): {error.msg}."
        ) from error

    if not isinstance(result, dict) or set(result) != {"answers"}:
        raise ValueError("IRZ category response must contain only an 'answers' array.")
    if not isinstance(result["answers"], list):
        raise ValueError("IRZ category response 'answers' must be an array.")

    allowed_options = {}
    for category in categories:
        if not isinstance(category, dict) or not isinstance(category.get("name"), str):
            raise ValueError("Each IRZ category must have a name and options.")
        if category["name"] in allowed_options or not isinstance(
            category.get("options"), list
        ):
            raise ValueError("IRZ categories must have unique names and option arrays.")
        allowed_options[category["name"]] = {
            option["value"]: option["text"] for option in category["options"]
        }

    if len(result["answers"]) != len(allowed_options):
        raise ValueError("The model must return exactly one answer for every category.")

    required_fields = {
        "category",
        "value",
        "text",
        "confidence",
        "reasoning",
        "supporting_evidence",
    }
    answers_by_category = {}
    for answer in result["answers"]:
        if not isinstance(answer, dict) or set(answer) != required_fields:
            raise ValueError("Each IRZ answer must contain the required answer fields only.")
        category_name = answer["category"]
        if not isinstance(category_name, str) or category_name not in allowed_options:
            raise ValueError(f"Unknown IRZ answer category: {category_name!r}.")
        if category_name in answers_by_category:
            raise ValueError(f"The model returned category {category_name!r} more than once.")
        value = answer["value"]
        if type(value) is not int or value not in allowed_options[category_name]:
            raise ValueError(
                f"The selected value for {category_name!r} was not offered."
            )
        if answer["text"] != allowed_options[category_name][value]:
            raise ValueError(
                f"The selected text for {category_name!r} does not match its value."
            )
        if not isinstance(answer["confidence"], str) or answer["confidence"] not in {
            "High", "Medium", "Low"
        }:
            raise ValueError(f"Invalid confidence for {category_name!r}.")
        if not isinstance(answer["reasoning"], str) or not answer["reasoning"].strip():
            raise ValueError(f"Reasoning is required for {category_name!r}.")
        evidence_items = answer["supporting_evidence"]
        if not isinstance(evidence_items, list) or any(
            not isinstance(item, dict)
            or set(item) != {"source", "evidence"}
            or not isinstance(item["source"], str)
            or not isinstance(item["evidence"], str)
            for item in evidence_items
        ):
            raise ValueError(
                f"supporting_evidence for {category_name!r} must be an array "
                "of source/evidence objects."
            )
        answers_by_category[category_name] = answer

    if set(answers_by_category) != set(allowed_options):
        raise ValueError("The model response is missing one or more IRZ categories.")
    return [answers_by_category[category["name"]] for category in categories]


def check_strategic(url):

    with _STRATEGIC_SOLUTION_PATH.open(encoding="utf-8") as f:
        data = [json.loads(line) for line in f]

    paragraph = [record for record in data if record.get("gis_ref") in url]
    return paragraph

def _get_html(url):
    url = "".join(url.split())
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.text

def _questions(html):
    soup = BeautifulSoup(html, "html.parser")

    questions = []

    for card in soup.select("div.card.r_div"):

        category = None

        header = card.select_one(".card-header p b")

        if header:
            category = header.get_text(strip=True).replace(":", "")

        options = []

        for label in card.select(".card-body label"):

            radio = label.select_one("input.irz_check")

            if not radio:
                continue

            value = radio.get("data-check_val")

            text_node = label.select_one("p")

            if text_node:
                options.append({
                    "value": value,
                    "text": text_node.get_text(" ", strip=True)
                })

        if category:
            questions.append({
                "category": category,
                "options": options
            })
    return questions

def get_consultation_questions(url):
    html = _get_html(url)
    return _questions(html)