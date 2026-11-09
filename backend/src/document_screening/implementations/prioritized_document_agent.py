"""Prioritize consultation documents, then download and screen selected records."""

import json
import warnings
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Union

import yaml

from backend.src.utilities.document_parser import parse_pdf
from backend.src.utilities.llm_client import LLMClient
from ..tools.lpa_client import PublicAccessClient
from ..tools.utilities import merge_document_findings, parse_document_findings


_DEFAULT_IMPLEMENTATION_CONFIG = (
    Path(__file__).resolve().parents[3] / "environment" / "configs" / "document_screening.yaml"
)
_PRIORITIZATION_PROMPT = Path(__file__).parent.parent / "prompts" / "prioritize_documents.md"
_SCREENING_PROMPT = Path(__file__).parent.parent / "prompts" / "screen_documents.md"
_DOCUMENT_METADATA_FIELDS = ("id", "published", "type", "drawing_number", "description")
_PRIORITIES = {"must", "maybe", "skip"}


def _load_implementation_config(config_path: Union[str, Path]) -> dict:
    config_path = Path(config_path)
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
    allowed = {"max_concurrent_downloads", "include_maybe", "ignore_after"}
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

    ignore_after = settings.get("ignore_after")
    if isinstance(ignore_after, datetime):
        raise ValueError("ignore_after must be a date, not a datetime.")
    if isinstance(ignore_after, str):
        try:
            ignore_after = date.fromisoformat(ignore_after)
        except ValueError as error:
            raise ValueError("ignore_after must use YYYY-MM-DD format.") from error
    elif ignore_after is not None and not isinstance(ignore_after, date):
        raise ValueError("ignore_after must be a date or null.")

    lpa_settings = config["lpa"]
    lpa_allowed = {
        "request_interval_seconds",
        "retry_increment_seconds",
        "max_retries",
        "max_wait_seconds",
        "refresh_session_on_429",
        "session_refresh_wait_seconds",
        "max_session_refreshes",
    }
    if not isinstance(lpa_settings, dict) or lpa_settings.keys() - lpa_allowed:
        raise ValueError(
            f"LPA settings must be a mapping with only: {', '.join(sorted(lpa_allowed))}."
        )

    return {
        "max_concurrent_downloads": max_concurrent_downloads,
        "include_maybe": include_maybe,
        "ignore_after": ignore_after,
        "lpa_settings": lpa_settings,
    }


def _parse_prioritization(response: str, expected_ids: list[str]) -> list[dict]:
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
        if record["priority"] not in _PRIORITIES:
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


def _write_metadata(metadata: dict, output_directory: Path, application_key: str) -> None:
    metadata_path = output_directory / f"{application_key}-metadata.json"
    with metadata_path.open("w", encoding="utf-8") as output:
        json.dump(metadata, output, ensure_ascii=False, indent=2)
        output.write("\n")


def run_prioritized_document_screening(
    reference: str,
    base_url: str,
    model_config_path: Union[str, Path],
    output_directory: Union[str, Path],
    *,
    implementation_config_path: Optional[Union[str, Path]] = None,
    prioritization_prompt_path: Optional[Union[str, Path]] = None,
    prompt_path: Optional[Union[str, Path]] = None,
    dpi: int = 150,
    context_limit: Optional[int] = None,
) -> dict:
    """Rank metadata first, download selected records concurrently, then screen them."""
    if context_limit is not None and (
        type(context_limit) is not int or context_limit <= 0
    ):
        raise ValueError("context_limit must be a positive integer when set.")

    implementation_config_path = (
        Path(implementation_config_path)
        if implementation_config_path is not None
        else _DEFAULT_IMPLEMENTATION_CONFIG
    )
    settings = _load_implementation_config(implementation_config_path)
    prioritization_prompt_path = (
        Path(prioritization_prompt_path)
        if prioritization_prompt_path is not None
        else _PRIORITIZATION_PROMPT
    )
    prompt_path = Path(prompt_path) if prompt_path is not None else _SCREENING_PROMPT
    prioritization_system_prompt = prioritization_prompt_path.read_text(encoding="utf-8")
    system_prompt = prompt_path.read_text(encoding="utf-8")
    output_directory = Path(output_directory).resolve()

    with closing(PublicAccessClient(
        base_url=base_url,
        lpa_settings=settings["lpa_settings"],
    )) as lpa_client:
        print("[document screening] Starting application search.")
        application_key = lpa_client.search(reference)
        print(f"[document screening] Completed application search: {application_key}.")
        print("[document screening] Starting metadata retrieval.")
        metadata = lpa_client.get_metadata(
            application_key,
            output_directory,
            ignore_after=settings["ignore_after"],
        )
        documents = metadata["documents"]
        print(
            f"[document screening] Completed metadata retrieval: "
            f"{len(documents)} documents."
        )
        document_ids = [str(document["id"]) for document in documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("LPA metadata contains duplicate document IDs.")
        ranking_input = [
            {field: document.get(field) for field in _DOCUMENT_METADATA_FIELDS}
            for document in documents
        ]
        ranking_prompt = (
            "Prioritize every document in this application using only the supplied metadata.\n"
            + json.dumps({"documents": ranking_input}, ensure_ascii=False, indent=2)
        )

        print(f"[document screening] Starting document ranking for {len(documents)} documents.")
        with LLMClient(model_config_path) as ranking_client:
            ranking_response = ranking_client.generate(
                ranking_prompt,
                system_prompt=prioritization_system_prompt,
            )
            provider = ranking_client.config.provider
            model = ranking_client.config.model
        prioritization = _parse_prioritization(ranking_response, document_ids)
        priority_counts = {
            priority: sum(item["priority"] == priority for item in prioritization)
            for priority in sorted(_PRIORITIES)
        }
        print(f"[document screening] Completed document ranking: {priority_counts}.")
        prioritization_by_id = {item["id"]: item for item in prioritization}
        for document in documents:
            decision = prioritization_by_id[str(document["id"])]
            document["priority"] = decision["priority"]
            document["priority_reason"] = decision["reason"]
            document["priority_uncertain"] = decision["uncertain"]
            document["downloaded"] = False

        _write_metadata(metadata, output_directory, application_key)
        selected_ids = [
            item["id"] for item in prioritization
            if item["priority"] == "must"
            or (settings["include_maybe"] and item["priority"] == "maybe")
        ]
        print(
            f"[document screening] Starting downloads for {len(selected_ids)} "
            f"selected documents (maximum concurrency {settings['max_concurrent_downloads']})."
        )
        lpa_client.download_documents_concurrently(
            application_key,
            metadata,
            output_directory,
            selected_ids,
            max_workers=settings["max_concurrent_downloads"],
        )
        downloaded_count = sum(document.get("downloaded", False) for document in documents)
        print(
            f"[document screening] Completed downloads: "
            f"{downloaded_count}/{len(selected_ids)} selected documents."
        )

    attachments = []
    sources = []
    print("[document screening] Starting document preparation.")
    for document in metadata["documents"]:
        if not document.get("downloaded"):
            continue
        document_path = output_directory / document["saved_as"]
        source = {
            "id": document["id"],
            "saved_as": document["saved_as"],
            "pages": [],
        }
        if document_path.suffix.lower() == ".pdf":
            pages = parse_pdf(document_path, output_directory / "rendered-pages", dpi=dpi)
            if not pages:
                raise ValueError(f"Document {document['id']} contains no PDF pages.")
            for page in pages:
                attachments.append(page.image_path)
                source["pages"].append({
                    "page_number": page.page_number,
                    "text": page.text,
                    "attachment_number": len(attachments),
                })
        else:
            attachments.append(document_path)
            source["attachment_number"] = len(attachments)
        sources.append(source)
    print(
        f"[document screening] Completed document preparation: "
        f"{len(sources)} downloaded documents, {len(attachments)} attachments."
    )

    downloaded_warnings = [
        f"Document {document['id']} was not downloaded: {document['download_error']}"
        for document in metadata["documents"]
        if "download_error" in document
    ]
    model_metadata = {
        **metadata,
        "documents": [
            {
                key: value for key, value in document.items()
                if key not in {"url", "download_error"}
            }
            for document in metadata["documents"]
        ],
    }
    prompt = (
        "Application metadata and downloaded document evidence:\n"
        + json.dumps({"metadata": model_metadata, "sources": sources}, ensure_ascii=False, indent=2)
        + "\n\nAttachments follow in the numbered order recorded above. "
        "PDF attachments are page images; extracted page text is included above."
    )
    with LLMClient(model_config_path) as llm_client:
        if context_limit is not None:
            estimated_text_tokens = (len(system_prompt) + len(prompt) + 3) // 4
            if estimated_text_tokens + llm_client.config.max_output_tokens >= context_limit * 0.8:
                warnings.warn(
                    "Estimated text tokens plus reserved output approach the configured "
                    "context limit. This estimate excludes attachment tokens and may "
                    "underestimate actual usage. No documents will be omitted.",
                    RuntimeWarning,
                    stacklevel=2,
                )
        print("[document screening] Starting evidence screening.")
        response = llm_client.generate(
            prompt,
            system_prompt=system_prompt,
            attachments=attachments,
        )
        print("[document screening] Completed evidence screening.")

    try:
        document_findings = parse_document_findings(response)
        document_findings["limitations"] = list(document_findings["limitations"])
        document_findings["limitations"].extend(downloaded_warnings)
        final_output = merge_document_findings(metadata, document_findings)
    except ValueError as error:
        response_path = output_directory / f"{application_key}-prioritized-response.txt"
        with response_path.open("x", encoding="utf-8") as output:
            output.write(response)
        raise ValueError(
            f"Could not use the model response: {error} "
            f"Raw response saved to {response_path}."
        ) from error

    result = {
        "reference": reference,
        "id": application_key,
        "metadata": metadata,
        "prioritization": prioritization,
        "prioritization_response": ranking_response,
        "document_findings": document_findings,
        "final_output": final_output,
        "sources": sources,
        "provider": provider,
        "model": model,
        "implementation_config_path": str(implementation_config_path.resolve()),
        "prioritization_prompt_path": str(prioritization_prompt_path.resolve()),
        "prompt_path": str(prompt_path.resolve()),
        "response": response,
    }

    result_path = output_directory / f"{application_key}-prioritized-document-screening.json"
    final_output_path = output_directory / f"{application_key}-prioritized-final-output.json"
    print("[document screening] Starting output save.")
    with final_output_path.open("x", encoding="utf-8") as output:
        json.dump(final_output, output, ensure_ascii=False, indent=2)
        output.write("\n")
    with result_path.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(
        f"[document screening] Completed output save: "
        f"{result_path.name} and {final_output_path.name}."
    )
    return result
