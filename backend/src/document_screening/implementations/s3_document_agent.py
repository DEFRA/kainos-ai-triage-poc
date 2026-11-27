"""Prioritize and screen consultation documents, retaining artifacts in S3.

Screening uses three model calls: prioritization of the document list,
batched pre-screening of the downloaded documents, and a final screening call
that consolidates the batch outputs into the validated findings schema.
"""

import contextvars
import json
import tempfile
import warnings
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any, Optional, Union
from zipfile import BadZipFile

from docx.opc.exceptions import PackageNotFoundError
from backend.src.utilities.document_parser import (
    parse_doc,
    parse_docx,
    parse_pdf,
    parse_rtf,
    parse_xlsx,
)
from backend.src.utilities.llm_client import LLMClient, ModelConfig
from backend.src.utilities.s3_utils import create_s3_client, put_object_with_metadata
from ..tools.lpa_client import PublicAccessClient
from ..tools.utilities import (
    add_polygon_metadata,
    clean_document_pages,
    clean_document_text,
    load_prioritization_config,
    match_document_priority_rule,
    merge_document_findings,
    parse_document_findings,
    parse_prioritization,
)


_DEFAULT_IMPLEMENTATION_CONFIG = (
    Path(__file__).resolve().parents[4] / "environment" / "configs" / "document_screening.yaml"
)
_PRIORITIZATION_PROMPT = Path(__file__).parent.parent / "prompts" / "prioritize_documents.md"
_SCREENING_PROMPT = Path(__file__).parent.parent / "prompts" / "screen_documents.md"
_CONSOLIDATION_PROMPT = Path(__file__).parent.parent / "prompts" / "consolidate_findings.md"
_DOCUMENT_METADATA_FIELDS = ("id", "published", "type", "drawing_number", "description")
MODEL_CALLS = ("prioritization", "pre_screening", "screening")


def _resolve_model_configs(
    model_configs: Union[str, Path, Mapping[str, Union[str, Path]]],
) -> dict[str, Path]:
    if isinstance(model_configs, (str, Path)):
        if not str(model_configs).strip():
            raise ValueError("model_configs must not be an empty path.")
        print(
            "[document screening] Detected a single model config; using it for "
            f"{', '.join(MODEL_CALLS)}: {Path(model_configs).name}."
        )
        return {call: Path(model_configs) for call in MODEL_CALLS}
    if not isinstance(model_configs, Mapping) or set(model_configs) != set(MODEL_CALLS):
        raise ValueError(
            "model_configs must be one model config path or a mapping with exactly: "
            f"{', '.join(MODEL_CALLS)}."
        )
    for call in MODEL_CALLS:
        if not isinstance(model_configs[call], (str, Path)) or not str(model_configs[call]).strip():
            raise ValueError(f"Model config for {call} must be a non-empty path.")
    resolved = {call: Path(model_configs[call]) for call in MODEL_CALLS}
    print(
        "[document screening] Detected separate model configs: "
        + ", ".join(f"{call}={path.name}" for call, path in resolved.items()) + "."
    )
    return resolved


def _warn_if_near_context_limit(
    llm_client: LLMClient, context_limit: Optional[int], system_prompt: str, prompt: str,
) -> None:
    if context_limit is None:
        return
    estimated_text_tokens = (len(system_prompt) + len(prompt) + 3) // 4
    if estimated_text_tokens + llm_client.config.max_output_tokens >= context_limit * 0.8:
        warnings.warn(
            "Estimated text tokens plus reserved output approach the configured "
            "context limit. This estimate excludes attachment tokens and may "
            "underestimate actual usage. No documents will be omitted.",
            RuntimeWarning, stacklevel=3,
        )


def _upload_artifacts(
    s3_client: Any, bucket: str, prefix: str, directory: Path, result_name: Optional[str] = None,
) -> dict[str, dict[str, Any]]:
    paths = sorted(path for path in directory.rglob("*") if path.is_file())
    if result_name is not None:
        paths.sort(key=lambda path: path.relative_to(directory).as_posix() == result_name)
    objects = {}
    for path in paths:
        relative_path = path.relative_to(directory).as_posix()
        key = f"{prefix}/{relative_path}"
        try:
            objects[relative_path] = put_object_with_metadata(
                s3_client, bucket, key, path.read_bytes()
            )
        except Exception as error:
            raise RuntimeError(f"Could not upload artifact to s3://{bucket}/{key}.") from error
    return objects


def run_s3_document_screening(
    reference: str,
    base_url: str,
    model_configs: Union[str, Path, Mapping[str, Union[str, Path]]],
    *,
    bucket: str,
    prefix: str,
    s3_client: Any = None,
    implementation_config_path: Optional[Union[str, Path]] = None,
    prioritization_prompt_path: Optional[Union[str, Path]] = None,
    prompt_path: Optional[Union[str, Path]] = None,
    consolidation_prompt_path: Optional[Union[str, Path]] = None,
    dpi: int = 150,
    context_limit: Optional[int] = None,
    ignore_after: Optional[date] = None,
) -> dict:
    """Screen prioritized documents and store the generated artifacts under an S3 prefix.

    model_configs is either one model config path, used for all three model
    calls, or a mapping with a path for each of prioritization, pre_screening
    and screening. prompt_path is the extraction prompt sent with every
    pre-screening batch; its output is not validated. The consolidation prompt
    plus the extraction prompt instruct the final screening call, whose output
    is validated as before.
    """
    if not isinstance(bucket, str) or not bucket.strip():
        raise ValueError("bucket must be a non-empty string.")
    if not isinstance(prefix, str) or not prefix.strip().strip("/"):
        raise ValueError("prefix must be a non-empty S3 run prefix.")
    bucket = bucket.strip()
    prefix = prefix.strip().strip("/")
    if context_limit is not None and (type(context_limit) is not int or context_limit <= 0):
        raise ValueError("context_limit must be a positive integer when set.")
    model_config_paths = _resolve_model_configs(model_configs)

    implementation_config_path = (
        Path(implementation_config_path)
        if implementation_config_path is not None else _DEFAULT_IMPLEMENTATION_CONFIG
    )
    settings = load_prioritization_config(implementation_config_path)
    prioritization_prompt_path = (
        Path(prioritization_prompt_path)
        if prioritization_prompt_path is not None else _PRIORITIZATION_PROMPT
    )
    prompt_path = Path(prompt_path) if prompt_path is not None else _SCREENING_PROMPT
    consolidation_prompt_path = (
        Path(consolidation_prompt_path)
        if consolidation_prompt_path is not None else _CONSOLIDATION_PROMPT
    )
    prioritization_system_prompt = prioritization_prompt_path.read_text(encoding="utf-8")
    pre_screening_system_prompt = prompt_path.read_text(encoding="utf-8")
    # The extraction prompt defines the fields and output schema the consolidation must return.
    screening_system_prompt = (
        consolidation_prompt_path.read_text(encoding="utf-8")
        + "\n\n# Extraction instructions used for the pre-screening batches\n\n"
        + pre_screening_system_prompt
    )
    # Attachment support depends on the model that receives the documents.
    pre_screening_provider = ModelConfig.from_yaml(model_config_paths["pre_screening"]).provider
    if s3_client is None:
        s3_client = create_s3_client()

    with tempfile.TemporaryDirectory() as temporary_directory:
        output_directory = Path(temporary_directory)
        try:
            with closing(PublicAccessClient(
                base_url=base_url, lpa_settings=settings["lpa_settings"],
            )) as lpa_client:
                print("[document screening] Starting application search.")
                application_key = lpa_client.search(reference)
                print(f"[document screening] Completed application search: {application_key}.")
                metadata = lpa_client.get_metadata(
                    application_key, output_directory, ignore_after=ignore_after,
                )
                documents = metadata["documents"]
                document_ids = [str(document["id"]) for document in documents]
                if len(document_ids) != len(set(document_ids)):
                    raise ValueError("LPA metadata contains duplicate document IDs.")

                rule_decisions = {}
                for document in documents:
                    rule = match_document_priority_rule(
                        document,
                        settings["must_document_patterns"],
                        settings["skip_document_type_patterns"],
                    )
                    if rule is not None:
                        rule_decisions[str(document["id"])] = {
                            "id": str(document["id"]), **rule, "uncertain": False, "source": "rule",
                        }
                rule_counts = [record["priority"] for record in rule_decisions.values()]
                print(
                    f"[document screening] Document rules matched {len(rule_decisions)} of "
                    f"{len(documents)} documents: {rule_counts.count('must')} must, "
                    f"{rule_counts.count('skip')} skip."
                )

                # The model sees short numbers instead of long LPA IDs, which it
                # otherwise tends to repeat, mistype or invent.
                ranked_documents = [
                    document for document in documents if str(document["id"]) not in rule_decisions
                ]
                number_to_id = {
                    str(number): str(document["id"])
                    for number, document in enumerate(ranked_documents, start=1)
                }
                prioritization_warnings = []
                ranking_response = None
                prioritization_usage = None
                model_decisions = {}
                if ranked_documents:
                    ranking_input = [
                        {
                            **{field: document.get(field) for field in _DOCUMENT_METADATA_FIELDS},
                            "id": number,
                        }
                        for number, document in enumerate(ranked_documents, start=1)
                    ]
                    ranking_prompt = (
                        "Prioritize every document in this application using only the supplied metadata.\n"
                        + json.dumps({"documents": ranking_input}, ensure_ascii=False, indent=2)
                    )
                    print(
                        "[document screening] Starting document ranking for "
                        f"{len(ranked_documents)} documents."
                    )
                    with LLMClient(model_config_paths["prioritization"]) as ranking_client:
                        ranking_response = ranking_client.generate(
                            ranking_prompt, system_prompt=prioritization_system_prompt,
                        )
                        models = {"prioritization": {
                            "provider": ranking_client.config.provider,
                            "model": ranking_client.config.model,
                        }}
                        prioritization_usage = (
                            ranking_client.last_usage
                            if isinstance(ranking_client.last_usage, dict) else None
                        )
                    # Saved before parsing so a rejected ranking can still be inspected in S3.
                    (output_directory / f"{application_key}-prioritization-response.txt").write_text(
                        ranking_response, encoding="utf-8",
                    )
                    ranked, ranking_warnings = parse_prioritization(
                        ranking_response, list(number_to_id),
                    )
                    for record in ranked:
                        real_id = number_to_id[record["id"]]
                        model_decisions[real_id] = {**record, "id": real_id, "source": "model"}
                    for warning in ranking_warnings:
                        if "document_ids" in warning:
                            warning["document_ids"] = [
                                number_to_id[number] for number in warning["document_ids"]
                            ]
                        prioritization_warnings.append(warning)
                else:
                    print("[document screening] No documents left for model ranking; skipping the ranking call.")
                    prioritization_config = ModelConfig.from_yaml(model_config_paths["prioritization"])
                    models = {"prioritization": {
                        "provider": prioritization_config.provider,
                        "model": prioritization_config.model,
                    }}
                if documents and rule_counts.count("skip") == len(documents):
                    prioritization_warnings.append({
                        "type": "all_documents_skipped_by_rules",
                        "document_ids": document_ids,
                        "message": "Every document matched a skip rule, so none were ranked or downloaded.",
                    })
                for warning in prioritization_warnings:
                    affected = warning.get("document_ids", warning.get("returned_ids", []))
                    print(
                        f"[document screening] WARNING: {warning['message']} "
                        f"({len(affected)}: {', '.join(affected)})"
                    )
                if prioritization_warnings:
                    metadata["prioritization_warnings"] = prioritization_warnings

                prioritization = [
                    rule_decisions.get(document_id) or model_decisions[document_id]
                    for document_id in document_ids
                ]
                prioritization_by_id = {item["id"]: item for item in prioritization}
                for document in documents:
                    decision = prioritization_by_id[str(document["id"])]
                    document["priority"] = decision["priority"]
                    document["priority_reason"] = decision["reason"]
                    document["priority_uncertain"] = decision["uncertain"]
                    document["priority_source"] = decision["source"]
                    if decision.get("missing_from_response"):
                        document["priority_missing_from_response"] = True
                    if decision.get("duplicate_in_response"):
                        document["priority_duplicate_in_response"] = True
                    document["downloaded"] = False

                metadata_path = output_directory / f"{application_key}-metadata.json"
                with metadata_path.open("w", encoding="utf-8") as output:
                    json.dump(metadata, output, ensure_ascii=False, indent=2)
                    output.write("\n")
                selected_ids = [
                    item["id"] for item in prioritization
                    if item["priority"] == "must"
                    or (settings["include_maybe"] and item["priority"] == "maybe")
                ]
                print(f"[document screening] Starting downloads for {len(selected_ids)} documents.")
                lpa_client.download_documents_concurrently(
                    application_key, metadata, output_directory, selected_ids,
                    max_workers=settings["max_concurrent_downloads"],
                )

            attachments = []
            sources = []
            preparation_warnings = []
            for document in documents:
                if not document.get("downloaded"):
                    continue
                document_path = output_directory / document["saved_as"]
                source = {"id": document["id"], "saved_as": document["saved_as"], "pages": []}
                suffix = document_path.suffix.lower()
                if suffix == ".pdf":
                    pages = parse_pdf(
                        document_path,
                        output_directory / "rendered-pages",
                        dpi=dpi,
                        image_format="jpeg",
                        jpeg_quality=90,
                        render_images=settings["include_attachments"],
                    )
                    if not pages:
                        raise ValueError(f"Document {document['id']} contains no PDF pages.")
                    page_texts = [page.text for page in pages]
                    if settings["clean_document_text"]:
                        page_texts = clean_document_pages(page_texts)
                    omitted_visual_pages = []
                    for page, page_text in zip(pages, page_texts):
                        page_source = {
                            "page_number": page.page_number,
                            "text": page_text,
                        }
                        if settings["include_attachments"] and (
                            page.has_visual_content or not page.text.strip()
                        ):
                            if page.image_path is None:
                                raise RuntimeError("PDF page image was not rendered for attachment.")
                            attachments.append(page.image_path)
                            page_source["attachment_number"] = len(attachments)
                        elif page.has_visual_content:
                            omitted_visual_pages.append(page.page_number)
                        source["pages"].append(page_source)
                    if omitted_visual_pages:
                        preparation_warnings.append(
                            f"Document {document['id']} has non-text visual content on PDF pages "
                            f"{', '.join(map(str, omitted_visual_pages))}; those page images were "
                            "not sent because include_attachments is false."
                        )
                elif suffix in {".docx", ".xlsx"}:
                    try:
                        if suffix == ".docx":
                            text = parse_docx(document_path)
                            if not text.strip():
                                raise ValueError("Document contains no extractable text.")
                            source["text"] = text
                        else:
                            source["sheets"] = parse_xlsx(document_path)
                    except (OSError, ValueError, BadZipFile, PackageNotFoundError) as error:
                        detail = str(error).replace(str(document_path), document["saved_as"])
                        warning = (
                            f"Document {document['id']} ({document['saved_as']}) could not be extracted: "
                            f"{type(error).__name__}: {detail}"
                        )
                        source["extraction_error"] = warning
                        preparation_warnings.append(warning)
                elif suffix in {".jpg", ".jpeg", ".png", ".webp"} or (
                    pre_screening_provider == "bedrock" and suffix == ".gif"
                ):
                    if settings["include_attachments"]:
                        attachments.append(document_path)
                        source["attachment_number"] = len(attachments)
                    else:
                        warning = (
                            f"Document {document['id']} ({document['saved_as']}) is an image and "
                            "was not sent because include_attachments is false; no OCR was performed."
                        )
                        source["extraction_error"] = warning
                        preparation_warnings.append(warning)
                elif suffix in {".csv", ".doc", ".xls", ".html", ".txt", ".md", ".rtf"}:
                    if settings["include_attachments"] and pre_screening_provider == "bedrock":
                        attachments.append(document_path)
                        source["attachment_number"] = len(attachments)
                    elif settings["include_attachments"]:
                        warning = (
                            f"Document {document['id']} ({document['saved_as']}) has unsupported file "
                            f"format {suffix} for {pre_screening_provider} and was not sent to the model."
                        )
                        source["extraction_error"] = warning
                        preparation_warnings.append(warning)
                    else:
                        try:
                            if suffix == ".doc":
                                source["text"] = parse_doc(document_path)
                            elif suffix == ".rtf":
                                source["text"] = parse_rtf(document_path)
                            elif suffix in {".csv", ".html", ".txt", ".md"}:
                                source["text"] = document_path.read_text(encoding="utf-8")
                            else:
                                raise ValueError("No text extractor is available for this format.")
                        except (OSError, UnicodeError, ValueError, RuntimeError) as error:
                            warning = (
                                f"Document {document['id']} ({document['saved_as']}) could not be "
                                f"sent as text: {type(error).__name__}: {error}"
                            )
                            source["extraction_error"] = warning
                            preparation_warnings.append(warning)
                else:
                    warning = (
                        f"Document {document['id']} ({document['saved_as']}) has unsupported file format "
                        f"{suffix or '(no extension)'} and was not sent to the model."
                    )
                    source["extraction_error"] = warning
                    preparation_warnings.append(warning)
                if settings["clean_document_text"] and isinstance(source.get("text"), str):
                    source["text"] = clean_document_text(source["text"])
                sources.append(source)

            downloaded_warnings = [
                f"Document {document['id']} was not downloaded: {document['download_error']}"
                for document in documents if "download_error" in document
            ]
            model_metadata = {
                **metadata,
                "documents": [
                    {key: value for key, value in document.items()
                     if key not in {"url", "download_error"}}
                    for document in documents
                ],
            }
            # Batches keep document order and close at pre_screening_batch_size
            # documents or before exceeding max_pre_screening_batch_characters;
            # a single larger document is screened alone. Attachment numbers are
            # renumbered within each batch so they match that call.
            batch_size = settings["pre_screening_batch_size"]
            max_batch_characters = settings["max_pre_screening_batch_characters"]
            source_groups = []
            group_characters = []
            for source in sources:
                source_characters = len(json.dumps(source, ensure_ascii=False, indent=2))
                if source_characters > max_batch_characters:
                    print(
                        f"[document screening] WARNING: Document {source['id']} has "
                        f"{source_characters:,} characters, more than "
                        f"max_pre_screening_batch_characters; screening it alone."
                    )
                if source_groups and (
                    len(source_groups[-1]) < batch_size
                    and group_characters[-1] + source_characters <= max_batch_characters
                ):
                    source_groups[-1].append(source)
                    group_characters[-1] += source_characters
                else:
                    source_groups.append([source])
                    group_characters.append(source_characters)
            if not source_groups:
                source_groups, group_characters = [[]], [0]
            batches = []
            for batch_number, (batch_sources, batch_characters) in enumerate(
                zip(source_groups, group_characters), start=1,
            ):
                batch_attachments = []
                for source in batch_sources:
                    source["pre_screening_batch"] = batch_number
                    for item in (source, *source["pages"]):
                        if "attachment_number" in item:
                            batch_attachments.append(attachments[item["attachment_number"] - 1])
                            item["attachment_number"] = len(batch_attachments)
                attachment_instructions = (
                    "Attachments follow in the numbered order recorded above. "
                    "PDF page images are attached only when a page contains non-text PDF objects "
                    "or has no extractable text. "
                ) if batch_attachments else "No file attachments are included. "
                batch_prompt = (
                    f"Pre-screening batch {batch_number} of {len(source_groups)}. The other "
                    "downloaded documents are screened in separate batches, so record only what "
                    "the application metadata and the documents in this batch show.\n\n"
                    "Application metadata and downloaded document evidence:\n"
                    + json.dumps({
                        "metadata": model_metadata,
                        "sources": batch_sources,
                    }, ensure_ascii=False, indent=2)
                    + "\n\n" + attachment_instructions
                    + "Extracted text for all PDF pages is included above. "
                    "DOCX text and XLSX sheet/cell values are included in the source JSON above."
                )
                batches.append({
                    "batch_number": batch_number,
                    "document_ids": [str(source["id"]) for source in batch_sources],
                    "prompt": batch_prompt,
                    "attachments": batch_attachments,
                    "document_characters": batch_characters,
                    "response_artifact": f"pre-screening/batch-{batch_number:02d}-response.txt",
                })

            def pre_screen_batch(batch: dict) -> tuple[str, Optional[dict], dict]:
                with LLMClient(model_config_paths["pre_screening"]) as batch_client:
                    _warn_if_near_context_limit(
                        batch_client, context_limit, pre_screening_system_prompt, batch["prompt"],
                    )
                    batch_response = batch_client.generate(
                        batch["prompt"],
                        system_prompt=pre_screening_system_prompt,
                        attachments=batch["attachments"],
                    )
                    batch_usage = (
                        batch_client.last_usage
                        if isinstance(batch_client.last_usage, dict) else None
                    )
                    batch_model = {
                        "provider": batch_client.config.provider,
                        "model": batch_client.config.model,
                    }
                response_path = output_directory / batch["response_artifact"]
                response_path.parent.mkdir(exist_ok=True)
                response_path.write_text(batch_response, encoding="utf-8")
                print(
                    f"[document screening] Completed pre-screening batch "
                    f"{batch['batch_number']} of {len(batches)}."
                )
                return batch_response, batch_usage, batch_model

            print(
                f"[document screening] Starting pre-screening of {len(sources)} documents in "
                f"{len(batches)} batches (up to {settings['max_concurrent_pre_screening_calls']} "
                "concurrent calls)."
            )
            with ThreadPoolExecutor(
                max_workers=settings["max_concurrent_pre_screening_calls"],
            ) as executor:
                # Each call runs in a copy of this context so LLM usage collection still applies.
                futures = [
                    executor.submit(contextvars.copy_context().run, pre_screen_batch, batch)
                    for batch in batches
                ]
                try:
                    for batch, future in zip(batches, futures):
                        batch["response"], batch["usage"], models["pre_screening"] = future.result()
                except BaseException:
                    for future in futures:
                        future.cancel()
                    raise
            pre_screening = [
                {key: value for key, value in batch.items() if key not in {"prompt", "attachments"}}
                for batch in batches
            ]

            screening_prompt = (
                "Application metadata and document preparation warnings:\n"
                + json.dumps({
                    "metadata": model_metadata,
                    "preparation_warnings": preparation_warnings,
                }, ensure_ascii=False, indent=2)
                + "\n\nPre-screening outputs:\n"
                + "".join(
                    f"\n=== Pre-screening batch {batch['batch_number']} of {len(batches)} "
                    f"(document IDs: {', '.join(batch['document_ids']) or 'none'}) ===\n"
                    f"{batch['response']}\n"
                    for batch in batches
                )
            )
            with LLMClient(model_config_paths["screening"]) as llm_client:
                _warn_if_near_context_limit(
                    llm_client, context_limit, screening_system_prompt, screening_prompt,
                )
                print("[document screening] Starting evidence screening (consolidation).")
                response = llm_client.generate(
                    screening_prompt, system_prompt=screening_system_prompt,
                )
                models["screening"] = {
                    "provider": llm_client.config.provider,
                    "model": llm_client.config.model,
                }
                screening_usage = (
                    llm_client.last_usage
                    if isinstance(llm_client.last_usage, dict) else None
                )
            print("[document screening] Completed evidence screening (consolidation).")

            try:
                document_findings = parse_document_findings(response)
                processing_warnings = downloaded_warnings + preparation_warnings
                if isinstance(document_findings.get("limitations"), list):
                    document_findings["limitations"].extend(processing_warnings)
                elif processing_warnings:
                    document_findings["processing_limitations"] = processing_warnings
                findings_name = f"{application_key}-prioritized-document-findings.json"
                with (output_directory / findings_name).open("x", encoding="utf-8") as output:
                    json.dump(document_findings, output, ensure_ascii=False, indent=2)
                    output.write("\n")
                final_output = merge_document_findings(metadata, document_findings)
            except ValueError as error:
                response_name = f"{application_key}-prioritized-response.txt"
                (output_directory / response_name).write_text(response, encoding="utf-8")
                raise ValueError(
                    f"Could not use the model response: {error} "
                    f"Raw response artifact: s3://{bucket}/{prefix}/{response_name}."
                ) from error
            final_output = add_polygon_metadata(
                final_output, settings["default_polygon_area_square_metres"],
            )
            for source in sources:
                source["s3"] = {"bucket": bucket, "key": f"{prefix}/{source['saved_as']}"}
            result = {
                "reference": reference,
                "id": application_key,
                "metadata": metadata,
                "prioritization": prioritization,
                "prioritization_response": ranking_response,
                "document_findings": document_findings,
                "final_output": final_output,
                "sources": sources,
                "pre_screening": pre_screening,
                "models": {
                    call: {**models[call], "config_path": str(model_config_paths[call])}
                    for call in MODEL_CALLS
                },
                "implementation_config_path": str(implementation_config_path.resolve()),
                "prioritization_prompt_path": str(prioritization_prompt_path.resolve()),
                "prompt_path": str(prompt_path.resolve()),
                "consolidation_prompt_path": str(consolidation_prompt_path.resolve()),
                "response": response,
                "usage": {
                    "prioritization": prioritization_usage,
                    "pre_screening": [batch["usage"] for batch in pre_screening],
                    "screening": screening_usage,
                },
            }
            result_name = f"{application_key}-prioritized-document-screening.json"
            final_output_name = f"{application_key}-prioritized-final-output.json"
            for name, payload in ((final_output_name, final_output), (result_name, result)):
                with (output_directory / name).open("x", encoding="utf-8") as output:
                    json.dump(payload, output, ensure_ascii=False, indent=2)
                    output.write("\n")
        except Exception as screening_error:
            try:
                _upload_artifacts(s3_client, bucket, prefix, output_directory)
            except Exception as upload_error:
                raise ExceptionGroup(
                    "Document screening failed and its artifacts could not be uploaded.",
                    [screening_error, upload_error],
                ) from None
            screening_error.add_note(f"Available artifacts were uploaded under s3://{bucket}/{prefix}/.")
            raise

        objects = _upload_artifacts(s3_client, bucket, prefix, output_directory, result_name)
        result["storage"] = {"bucket": bucket, "prefix": prefix, "objects": objects}
        return result