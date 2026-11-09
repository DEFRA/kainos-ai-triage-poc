"""Retrieve consultation documents and submit their evidence to a model."""

import json
import warnings
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Optional, Union

from backend.src.utilities.llm_client import LLMClient

from backend.src.utilities.document_parser import parse_pdf
from ..tools.lpa_client import PublicAccessClient
from ..tools.utilities import merge_document_findings, parse_document_findings


_DOCUMENT_PROMPT = Path(__file__).parent.parent / "prompts" / "screen_documents.md"


def run_document_screening(
    reference: str,
    base_url: str,
    model_config_path: Union[str, Path],
    output_directory: Union[str, Path],
    *,
    prompt_path: Optional[Union[str, Path]] = None,
    dpi: int = 150,
    context_limit: Optional[int] = None,
    ignore_after: Optional[date] = None,
) -> dict:
    """Retrieve, prepare, and screen all documents together in one request.

    output_directory is the directory for this case, not a parent for all cases.
    The prompt controls response formatting; its JSON object is parsed and
    merged with the LPA metadata while the raw response is retained as text.
    context_limit optionally enables an approximate text-only budget warning;
    image/document token usage is model-specific and is not included.
    Files are never silently skipped or truncated. Existing output is not
    overwritten, and failures leave already downloaded/rendered files in place.
    """
    if context_limit is not None and (
        type(context_limit) is not int or context_limit <= 0
    ):
        raise ValueError("context_limit must be a positive integer when set.")
    prompt_path = Path(prompt_path) if prompt_path is not None else _DOCUMENT_PROMPT
    system_prompt = prompt_path.read_text(encoding="utf-8")
    output_directory = Path(output_directory).resolve()

    with closing(PublicAccessClient(base_url=base_url)) as lpa_client:
        print("[document screening] Starting application search.")
        application_key = lpa_client.search(reference)
        print(f"[document screening] Completed application search: {application_key}.")
        print("[document screening] Starting metadata retrieval.")
        metadata = lpa_client.get_metadata(
            application_key, output_directory, ignore_after=ignore_after
        )
        print(
            f"[document screening] Completed metadata retrieval: "
            f"{len(metadata['documents'])} documents."
        )
        print("[document screening] Starting document downloads.")
        lpa_client.download_documents(application_key, metadata, output_directory)
        print("[document screening] Completed document downloads.")

    attachments = []
    sources = []
    print(
        f"[document screening] Starting document preparation for "
        f"{len(metadata['documents'])} documents."
    )
    for document in metadata["documents"]:
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
        f"{len(sources)} documents, {len(attachments)} attachments."
    )

    model_metadata = {
        **metadata,
        "documents": [
            {key: value for key, value in document.items() if key != "url"}
            for document in metadata["documents"]
        ],
    }
    prompt = (
        "Application metadata and document evidence:\n"
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
        print("[document screening] Starting model screening.")
        response = llm_client.generate(
            prompt,
            system_prompt=system_prompt,
            attachments=attachments,
        )
        print("[document screening] Completed model screening.")
        provider = llm_client.config.provider
        model = llm_client.config.model

    try:
        document_findings = parse_document_findings(response)
        final_output = merge_document_findings(metadata, document_findings)
    except ValueError as error:
        response_path = output_directory / f"{application_key}-document-screening-response.txt"
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
        "document_findings": document_findings,
        "final_output": final_output,
        "sources": sources,
        "provider": provider,
        "model": model,
        "prompt_path": str(prompt_path.resolve()),
        "response": response,
    }

    result_path = output_directory / f"{application_key}-document-screening.json"
    final_output_path = output_directory / f"{application_key}-final-output.json"
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