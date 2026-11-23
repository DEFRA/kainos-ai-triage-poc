"""Write cleaned consultation emails for case intake.

Keeps each parent email's headers and body, replaces its attachments with the
non-checklist documents from the QC email, and logs each consultation.
Run from the project root: uv run python pre_processing/clean_consultation_emails.py
"""

import csv
import re
import sys
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.src.case_intake.tools.email_parser import (  # noqa: E402
    eml_attachment_bytes,
    iter_eml_attachment_parts,
    parse_eml_bytes,
)

INPUT_ROOT = PROJECT_ROOT / "data/temp/unnecessary"
OUTPUT_ROOT = PROJECT_ROOT / "data/temp/unnecessary_clean"
LOG_PATH = OUTPUT_ROOT / "preprocessing_log.csv"
DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".rtf"}
NON_EMAIL_EXTENSIONS = DOCUMENT_EXTENSIONS | {".zip", ".xlsx", ".xls", ".csv", ".txt", ".png", ".jpg", ".jpeg", ".gif"}
LOG_FIELDS = ["consultation_id", "status", "source", "kept", "removed", "note"]
EXCLUDED_NAME_TERMS = ("checklist", "response", "annex")
QC_PATTERN = re.compile(r"(?<![a-z])qc(?![a-z])", re.IGNORECASE)


@dataclass
class Attachment:
    filename: str
    content_type: str
    payload: bytes


def is_qc(text: str) -> bool:
    return bool(QC_PATTERN.search(text))


def is_qc_email(name: str, message: EmailMessage) -> bool:
    return is_qc(name) or is_qc(str(message.get("Subject", "")))


def is_kept_document(filename: str) -> bool:
    lower_name = filename.lower()
    return Path(lower_name).suffix in DOCUMENT_EXTENSIONS and not any(
        term in lower_name for term in EXCLUDED_NAME_TERMS
    )


def load_folder_emails(folder: Path) -> list[tuple[str, EmailMessage]]:
    # Extensionless files are emails too, so only known non-email types are skipped.
    return [
        (path.name, parse_eml_bytes(path.read_bytes()))
        for path in sorted(folder.iterdir())
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() not in NON_EMAIL_EXTENSIONS
    ]


def leaf_attachments(message: EmailMessage) -> list[Attachment]:
    attachments: list[Attachment] = []
    for index, part in enumerate(iter_eml_attachment_parts(message)):
        content_type = part.get_content_type()
        if content_type == "message/rfc822":
            nested = parse_eml_bytes(eml_attachment_bytes(part))
            attachments.extend(leaf_attachments(nested))
            continue
        filename = part.get_filename() or f"attachment_{index}"
        attachments.append(Attachment(filename, content_type, eml_attachment_bytes(part)))
    return attachments


def nested_qc_emails(parent: EmailMessage) -> list[tuple[str, EmailMessage]]:
    candidates = []
    for part in iter_eml_attachment_parts(parent):
        if part.get_content_type() != "message/rfc822":
            continue
        name = part.get_filename() or ""
        nested = parse_eml_bytes(eml_attachment_bytes(part))
        if is_qc_email(name, nested):
            candidates.append((f"nested:{name}", nested))
    return candidates


def build_clean_email(parent: EmailMessage, kept: list[Attachment]) -> EmailMessage:
    clean = EmailMessage()
    for header in ("Subject", "From", "To", "Date", "Message-ID"):
        value = parent.get(header)
        if value:
            clean[header] = str(value)

    body = parent.get_body(preferencelist=("plain", "html"))
    if body is None:
        clean.set_content("")
    else:
        clean.set_content(body.get_content(), subtype=body.get_content_subtype())

    for attachment in kept:
        maintype, _, subtype = attachment.content_type.partition("/")
        clean.add_attachment(
            attachment.payload,
            maintype=maintype,
            subtype=subtype,
            filename=attachment.filename,
        )
    return clean


def make_row(
    consultation_id: str,
    status: str,
    source: str = "",
    kept: list[str] | None = None,
    removed: list[str] | None = None,
    note: str = "",
) -> dict[str, str]:
    return {
        "consultation_id": consultation_id,
        "status": status,
        "source": source,
        "kept": "; ".join(kept or []),
        "removed": "; ".join(removed or []),
        "note": note,
    }


def process_consultation(folder: Path) -> list[dict[str, str]]:
    emails = load_folder_emails(folder)
    parents = [(name, msg) for name, msg in emails if not is_qc_email(name, msg)]
    standalone_qcs = [(name, msg) for name, msg in emails if is_qc_email(name, msg)]

    if not parents:
        return [make_row(folder.name, "error", note="no parent .eml found")]

    if len(parents) == 1:
        units = [(folder.name, parents[0][1])]
    else:
        units = [(f"{folder.name}_{index}", msg) for index, (_, msg) in enumerate(parents, 1)]

    return [process_unit(unit_id, parent, standalone_qcs) for unit_id, parent in units]


def process_unit(
    unit_id: str,
    parent: EmailMessage,
    standalone_qcs: list[tuple[str, EmailMessage]],
) -> dict[str, str]:
    parent_attachments = leaf_attachments(parent)
    candidates = [(f"file:{name}", msg) for name, msg in standalone_qcs]
    candidates += nested_qc_emails(parent)

    source_label, source_attachments = "parent", parent_attachments
    for label, qc_message in candidates:
        attachments = leaf_attachments(qc_message)
        if any(is_kept_document(a.filename) for a in attachments):
            source_label, source_attachments = label, attachments
            break

    kept = [a for a in source_attachments if is_kept_document(a.filename)]
    kept_ids = {id(a) for a in kept}
    removed = [f"parent: {a.filename}" for a in parent_attachments if id(a) not in kept_ids]
    if source_label != "parent":
        removed += [f"qc: {a.filename}" for a in source_attachments if id(a) not in kept_ids]

    if not kept:
        return make_row(
            unit_id,
            "skipped",
            source="none",
            removed=removed,
            note="no document found in QC email or parent",
        )

    status = "ok" if source_label != "parent" else "fallback"
    output_folder = OUTPUT_ROOT / unit_id
    output_folder.mkdir(parents=True, exist_ok=True)
    clean = build_clean_email(parent, kept)
    (output_folder / f"{unit_id} clean.eml").write_bytes(clean.as_bytes(policy=policy.default))

    return make_row(
        unit_id,
        status,
        source=source_label,
        kept=[a.filename for a in kept],
        removed=removed,
    )


def write_log(rows: list[dict[str, str]]) -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("w", newline="", encoding="utf-8") as log_file:
        writer = csv.DictWriter(log_file, fieldnames=LOG_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rows = []
    for folder in sorted(path for path in INPUT_ROOT.iterdir() if path.is_dir() and not path.name.startswith(".")):
        try:
            rows.extend(process_consultation(folder))
        except Exception as error:
            # One bad folder should not stop the batch; the error is written to the log.
            rows.append(make_row(folder.name, "error", note=f"{type(error).__name__}: {error}"))

    write_log(rows)
    counts = {status: sum(row["status"] == status for row in rows) for status in ("ok", "fallback", "skipped", "error")}
    print(f"Processed {len(rows)} consultations: {counts}")
    print(f"Log: {LOG_PATH}")


if __name__ == "__main__":
    # Optional: input and output folders, e.g. data/temp/necessary data/temp/necessary_clean
    if len(sys.argv) == 3:
        INPUT_ROOT = Path(sys.argv[1]).resolve()
        OUTPUT_ROOT = Path(sys.argv[2]).resolve()
        LOG_PATH = OUTPUT_ROOT / "preprocessing_log.csv"
    main()
