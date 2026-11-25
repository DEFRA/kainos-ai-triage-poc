import copy
import re
from urllib.parse import urlsplit


def _normalise_lpa_name(value: str) -> str:
    # 'lpa_name' column may omit spaces between words
    # 'assigned_current_lpa_name' column does not omit spaces between words
    # Ignore spacing, most punctuation and case when comparing names
    # E.g.:
    # _normalise_lpa_name("Adur Worthing")  # "adurworthing"
    # _normalise_lpa_name("AdurWorthing")   # "adurworthing"
    # _normalise_lpa_name("Adur-Worthing")  # "adurworthing"
    return "".join(re.findall(r"\w+", value.casefold()))


def _website_host(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    try:
        parsed = urlsplit(value if "://" in value else f"https://{value}")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            return None
        host = parsed.hostname.rstrip(".").casefold()
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host if "." in host else None


def _website_path(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value if "://" in value else f"https://{value}")
    return parsed.path.rstrip("/")


def deterministic_post_processing_lpa_website(
    case_intake_extractions_json,
    mapping,
    mapping_source: object,
) -> tuple[bool, list[str]]:
    """Deterministically replace extracted LPA websites using one case intake extractions JSON and spreadsheet rows.
    Deterministic > LLM invocation in terms of reduced overhead.
    Spreadsheet here refers to the Excel/CSV file containing IDOX LPA website mappings.

    Steps:
    1. Read each record's lpa_website and lpa_name (from case intake extractions JSON).

    2. Compare the extracted website with the spreadsheet's lpa_website column.
    For reference:
        https://planning.example.gov.uk/online-applications/?ref=123
                └──── hostname ───────┘└────── path ───────┘└ query ┘
    The function starts with the hostname because the extracted website URL might
        be a general council webpage, while the spreadsheet contains a more
        specific planning webpage.

    Sub-steps:
        a. Extract and normalise the hostname from the extracted website and each
        spreadsheet website. Ignore case, a leading "www." and trailing dots.
        If a URL has no scheme, treat it as HTTPS.

        b. Keep spreadsheet rows where the hostnames match exactly, or where one
        hostname is a subdomain of the other. For example, example.gov.uk
        matches planning.example.gov.uk. The comparison uses a dot boundary,
        so example.gov.uk does not match example.gov.uk.attacker.com.

        c. If the extracted URL has a path, compare it with the paths of the
        hostname-matched spreadsheet rows. Paths match if they are identical
        or if one is a parent path of the other. For example, /applications
        matches /applications/details. Trailing slashes are ignored, as are
        query strings and fragments.

        d. If one or more paths match, keep only those rows. If no paths match,
        retain the hostname-matched rows for the name comparison in step 3.

    3. Compare the extracted LPA name with the spreadsheet's lpa_name (old LPA name) and
        assigned_current_lpa_name (current LPA name due to any reorganisations) columns.
    Case, spaces and most punctuation are ignored during normalisation. For example,
        an extracted name of 'Adur Worthing' matches 'AdurWorthing' because both
        normalise to 'adurworthing'.
    The normalised name must match either column exactly; words are not removed
        and fuzzy matching is not used.
    If the name matches any spreadsheet rows, keep only those that also passed
        the website checks in step 2. If there are no name matches anywhere in the
        spreadsheet, leave the website candidates unchanged. If name matches exist
        but none are website candidates, retain the original website with a warning.

    4. Replace the website only if the remaining rows agree on one URL.
    Otherwise, retain the original value and report a warning.

    The case intake extractions JSON is updated in memory. The caller
    must save it separately (outside this function) for the changes to persist, for example to S3.
    For structured website fields, the original extraction is preserved
    and the spreadsheet is recorded as evidence.

    Returns:
        A tuple containing whether anything changed and a list of warnings.
    """
    changed = False
    warnings = []
    for record_number, record in enumerate(case_intake_extractions_json["records"], start=1):
        extraction = record["extractions"]
        website_field = extraction.get("lpa_website")
        if isinstance(website_field, dict):
            if website_field.get("status") in {"conflicting", "unclear", "not_stated"}:
                warnings.append(f"Record {record_number}: no unambiguous extracted website; retained original.")
                continue
            website = website_field.get("value")
        else:
            website = website_field

        host = _website_host(website)
        if not host:
            warnings.append(f"Record {record_number}: no valid extracted website; retained original.")
            continue

        candidates = []
        for row in mapping:
            candidate_host = _website_host(row["lpa_website"])
            if candidate_host and (
                host == candidate_host
                or host.endswith("." + candidate_host)
                or candidate_host.endswith("." + host)
            ):
                candidates.append(row)

        name_field = extraction.get("lpa_name")
        if isinstance(name_field, dict):
            if name_field.get("status") in {"conflicting", "unclear"}:
                warnings.append(f"Record {record_number}: conflicting LPA name; retained original.")
                continue
            name = name_field.get("value") if name_field.get("status") != "not_stated" else None
        else:
            name = name_field
        name_matches = []
        if isinstance(name, str) and name.strip():
            normalised_name = _normalise_lpa_name(name)
            name_matches = [
                row for row in mapping
                if normalised_name in {
                    _normalise_lpa_name(row["lpa_name"]),
                    _normalise_lpa_name(row["assigned_current_lpa_name"]),
                }
            ]

        path = _website_path(website)
        if path:
            path_matches = []
            for row in candidates:
                candidate_path = _website_path(row["lpa_website"])
                if candidate_path and (
                    path == candidate_path
                    or path.startswith(candidate_path + "/")
                    or candidate_path.startswith(path + "/")
                ):
                    path_matches.append(row)
            if path_matches:
                candidates = path_matches

        if name_matches:
            candidates = [row for row in candidates if row in name_matches]

        urls = {row["lpa_website"] for row in candidates}
        if len(urls) != 1:
            warnings.append(f"Record {record_number}: no unique website match; retained original.")
            continue

        url = next(iter(urls))
        if website == url:
            continue
        if isinstance(website_field, dict):
            original = copy.deepcopy(website_field.get("original_extraction", website_field))
            website_field.update({
                "value": url,
                "status": "stated",
                "confidence": "high",
                "reason": "Matched extracted website to the spreadsheet, using a matching LPA name when available.",
                "evidence": [{"source": str(mapping_source), "quote": url}],
                "original_extraction": original,
            })
        else:
            extraction["lpa_website"] = url
        changed = True

    return changed, warnings