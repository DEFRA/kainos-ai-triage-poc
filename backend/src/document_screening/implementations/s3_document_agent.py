"""Prioritize and screen consultation documents, retaining artifacts in S3."""

import json
import tempfile
import warnings
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any, Optional, Union

from backend.src.utilities.document_parser import parse_pdf
from backend.src.utilities.llm_client import LLMClient
from backend.src.utilities.s3_utils import create_s3_client, put_object_with_metadata
from ..tools.lpa_client import PublicAccessClient
from ..tools.utilities import (
    add_polygon_metadata,
    load_prioritization_config,
    merge_document_findings,
    parse_document_findings,
    parse_prioritization,
)


_DEFAULT_IMPLEMENTATION_CONFIG = (
    Path(__file__).resolve().parents[4] / "environment" / "configs" / "document_screening.yaml"
)
_PRIORITIZATION_PROMPT = Path(__file__).parent.parent / "prompts" / "prioritize_documents.md"
_SCREENING_PROMPT = Path(__file__).parent.parent / "prompts" / "screen_documents.md"
_DOCUMENT_METADATA_FIELDS = ("id", "published", "type", "drawing_number", "description")


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
    model_config_path: Union[str, Path],
    *,
    bucket: str,
    prefix: str,
    s3_client: Any = None,
    implementation_config_path: Optional[Union[str, Path]] = None,
    prioritization_prompt_path: Optional[Union[str, Path]] = None,
    prompt_path: Optional[Union[str, Path]] = None,
    dpi: int = 150,
    context_limit: Optional[int] = None,
    ignore_after: Optional[date] = None,
) -> dict:
    """Screen prioritized documents and store the generated artifacts under an S3 prefix."""
    if not isinstance(bucket, str) or not bucket.strip():
        raise ValueError("bucket must be a non-empty string.")
    if not isinstance(prefix, str) or not prefix.strip().strip("/"):
        raise ValueError("prefix must be a non-empty S3 run prefix.")
    bucket = bucket.strip()
    prefix = prefix.strip().strip("/")
    if context_limit is not None and (type(context_limit) is not int or context_limit <= 0):
        raise ValueError("context_limit must be a positive integer when set.")

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
    prioritization_system_prompt = prioritization_prompt_path.read_text(encoding="utf-8")
    system_prompt = prompt_path.read_text(encoding="utf-8")
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
                        ranking_prompt, system_prompt=prioritization_system_prompt,
                    )
                    provider = ranking_client.config.provider
                    model = ranking_client.config.model
                prioritization = parse_prioritization(ranking_response, document_ids)
                prioritization_by_id = {item["id"]: item for item in prioritization}
                for document in documents:
                    decision = prioritization_by_id[str(document["id"])]
                    document["priority"] = decision["priority"]
                    document["priority_reason"] = decision["reason"]
                    document["priority_uncertain"] = decision["uncertain"]
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
            for document in documents:
                if not document.get("downloaded"):
                    continue
                document_path = output_directory / document["saved_as"]
                source = {"id": document["id"], "saved_as": document["saved_as"], "pages": []}
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
                            RuntimeWarning, stacklevel=2,
                        )
                print("[document screening] Starting evidence screening.")
                response = llm_client.generate(
                    prompt, system_prompt=system_prompt, attachments=attachments,
                )

            try:
                document_findings = parse_document_findings(response)
                findings_name = f"{application_key}-prioritized-document-findings.json"
                with (output_directory / findings_name).open("x", encoding="utf-8") as output:
                    json.dump(document_findings, output, ensure_ascii=False, indent=2)
                    output.write("\n")
                document_findings["limitations"] = list(document_findings["limitations"])
                document_findings["limitations"].extend(downloaded_warnings)
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