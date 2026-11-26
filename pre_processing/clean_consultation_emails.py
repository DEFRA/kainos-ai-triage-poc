"""Write one cleaned email from the QC messages for each consultation.

Keeps the original message in each QC thread and eligible QC attachments.
Run from the project root: uv run python pre_processing/clean_consultation_emails.py
"""

import csv
import hashlib
import re
import shutil
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
    parse_email_file,
    parse_eml_bytes,
)

INPUT_ROOT = PROJECT_ROOT / "data/temp/unnecessary"
OUTPUT_ROOT = PROJECT_ROOT / "data/temp/unnecessary_clean"
LOG_PATH = OUTPUT_ROOT / "preprocessing_log.csv"
DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".rtf"}
NON_EMAIL_EXTENSIONS = DOCUMENT_EXTENSIONS | {".zip", ".xlsx", ".xls", ".csv", ".txt", ".png", ".jpg", ".jpeg", ".gif"}
LOG_FIELDS = ["consultation_id", "status", "source", "kept", "removed", "note"]
EXCLUDED_NAME_TERMS = ("checklist", "response", "annex")
NON_IDOX_CONSULTATIONS = dict.fromkeys((
    "537112", "537968", "538136", "538342", "538652", "538942", "538952",
    "539003", "539675", "539693", "539696", "539832", "540112", "540138",
    "540221", "540247", "540705", "540863", "541006", "541107", "542678",
    "542844", "543004", "543175", "543864", "544073", "544439", "544939",
    "545677", "546448", "546558", "546660", "546707", "546767", "546851",
    "547279", "547758", "547798", "548298", "548806", "549600", "550096",
    "550320", "551172", "551939", "552149", "552365", "552524", "553039",
    "553060", "553082", "553257", "555002", "556227", "556309", "557602",
    "557633", "557879", "558808", "559020", "559551", "559556", "560400", "539656"
), "skip")
QC_PATTERN = re.compile(r"(?<![a-z])qc(?![a-z])", re.IGNORECASE)
QC_BODY_PATTERN = re.compile(
    r"(?im)^\s*(?:(?:hi|hello)[,\s]+)?qc\s+(?:complete|completed)\b"
    r"|^\s*please\s+qc\b"
)
CONSULTATION_ID_PATTERN = re.compile(r"(?<!\d)(\d{6})(?!\d)")
FORWARDED_EMAIL_HEADERS = re.compile(
    r"(?im)^From:[^\r\n]*\r?\n"
    r"(?:(?:Sent|Date|To|Cc|Subject):[^\r\n]*\r?\n)+\s*"
)


@dataclass
class Attachment:
    filename: str
    content_type: str
    payload: bytes


def is_qc(text: str) -> bool:
    return bool(QC_PATTERN.search(text))


def is_qc_email(name: str, message: EmailMessage) -> bool:
    body = message.get_body(preferencelist=("plain",))
    body_text = str(body.get_content()) if body is not None else ""
    return (
        is_qc(name)
        or is_qc(str(message.get("Subject", "")))
        or bool(QC_BODY_PATTERN.search(body_text))
    )


def is_kept_document(filename: str) -> bool:
    lower_name = filename.lower()
    return Path(lower_name).suffix in DOCUMENT_EXTENSIONS and not any(
        term in lower_name for term in EXCLUDED_NAME_TERMS
    )


def consultation_id(name: str, message: EmailMessage) -> str | None:
    body = message.get_body(preferencelist=("plain",))
    values = [name, str(message.get("Subject", ""))]
    if body is not None:
        values.append(str(body.get_content()))
    for value in values:
        match = CONSULTATION_ID_PATTERN.search(value)
        if match:
            return match.group(1)
    return None


def original_email_parts(message: EmailMessage) -> tuple[dict[str, str], str]:
    body = message.get_body(preferencelist=("plain",))
    if body is None:
        return {}, ""

    text = str(body.get_content())
    matches = list(FORWARDED_EMAIL_HEADERS.finditer(text))
    if not matches:
        return {}, ""

    root_match = matches[-1]
    headers = {}
    header_names = {"from": "From", "to": "To", "subject": "Subject", "sent": "Date", "date": "Date"}
    for line in root_match.group().splitlines():
        name, separator, value = line.partition(":")
        output_name = header_names.get(name.strip().lower())
        if separator and output_name:
            headers[output_name] = value.strip()
    return headers, text[root_match.end():].strip()


def original_email_body(message: EmailMessage) -> str:
    return original_email_parts(message)[1]


def normalized_body(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def attachment_hash(attachment: Attachment) -> str:
    return hashlib.sha256(attachment.payload).hexdigest()


def unique_attachments(attachments: list[Attachment]) -> list[Attachment]:
    unique = []
    seen_hashes = set()
    for attachment in attachments:
        digest = attachment_hash(attachment)
        if digest not in seen_hashes:
            seen_hashes.add(digest)
            unique.append(attachment)
    return unique


def merged_qc_content(
    qc_emails: list[tuple[str, EmailMessage]],
) -> tuple[dict[str, str], str]:
    headers = {}
    bodies = []
    seen_bodies = set()
    for _, message in qc_emails:
        message_headers, body = original_email_parts(message)
        if not headers and message_headers:
            headers = message_headers
        normalized = normalized_body(body)
        if normalized and normalized not in seen_bodies:
            seen_bodies.add(normalized)
            bodies.append(body)
    return headers, "\n\n".join(bodies)


def parse_msg_email(path: Path) -> EmailMessage:
    parsed, _, attachment_payloads = parse_email_file(path)
    message = EmailMessage()
    headers = parsed["headers"]
    for output_name, source_name in (
        ("Subject", "subject"),
        ("Date", "datetime_raw"),
        ("Message-ID", "message_id"),
    ):
        value = headers.get(source_name)
        if value:
            message[output_name] = str(value).replace("\r", " ").replace("\n", " ")

    body = parsed["body"]
    if body.get("text"):
        message.set_content(body["text"])
    elif body.get("html"):
        message.set_content(body["html"], subtype="html")
    else:
        message.set_content("")

    for attachment, payload in zip(parsed["attachments"], attachment_payloads):
        if payload is None:
            continue
        content_type = attachment.get("mime_type", "application/octet-stream")
        maintype, separator, subtype = content_type.partition("/")
        if not separator:
            maintype, subtype = "application", "octet-stream"
        message.add_attachment(
            payload,
            maintype=maintype,
            subtype=subtype,
            filename=attachment.get("filename") or "attachment",
        )
    return message


def load_folder_emails(folder: Path) -> list[tuple[str, EmailMessage]]:
    emails = []
    for path in sorted(folder.iterdir()):
        if (
            not path.is_file()
            or path.name.startswith(".")
            or path.suffix.lower() in NON_EMAIL_EXTENSIONS
        ):
            continue
        if path.suffix.lower() == ".msg":
            message = parse_msg_email(path)
        else:
            message = parse_eml_bytes(path.read_bytes())
        emails.append((path.name, message))
    return emails


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


def build_clean_email(
    headers: dict[str, str],
    kept: list[Attachment],
    body_text: str,
) -> EmailMessage:
    clean = EmailMessage()
    for header in ("Subject", "From", "To", "Date"):
        value = headers.get(header)
        if value:
            clean[header] = str(value)

    clean.set_content(body_text)

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
    groups = collect_qc_groups(folder)
    if not groups:
        return [make_row(folder.name, "skipped", note="no QC email found")]
    return [
        process_qc_group(case_id, group)
        for case_id, group in sorted(groups.items())
    ]


def collect_qc_groups(folder: Path) -> dict[str, list[tuple[str, EmailMessage]]]:
    emails = load_folder_emails(folder)
    qc_emails = [(name, message) for name, message in emails if is_qc_email(name, message)]
    if not qc_emails:
        return {}

    folder_match = CONSULTATION_ID_PATTERN.search(folder.name)
    fallback_id = folder_match.group(1) if folder_match else folder.name
    groups: dict[str, list[tuple[str, EmailMessage]]] = {}
    seen_messages = set()

    def add_qc_email(name: str, message: EmailMessage, default_id: str) -> None:
        if id(message) in seen_messages:
            return
        seen_messages.add(id(message))
        case_id = consultation_id(name, message) or default_id
        groups.setdefault(case_id, []).append((name, message))
        for nested_name, nested_message in nested_qc_emails(message):
            add_qc_email(nested_name, nested_message, case_id)

    for name, message in qc_emails:
        add_qc_email(name, message, fallback_id)

    return groups


def process_qc_group(
    case_id: str,
    qc_emails: list[tuple[str, EmailMessage]],
) -> dict[str, str]:
    all_attachments = [
        (source, attachment)
        for source, message in qc_emails
        for attachment in leaf_attachments(message)
    ]
    eligible_attachments = [
        attachment
        for _, attachment in all_attachments
        if is_kept_document(attachment.filename)
    ]
    kept = unique_attachments(eligible_attachments)
    kept_ids = {id(attachment) for attachment in kept}
    removed = [
        f"{source}: {attachment.filename}"
        for source, attachment in all_attachments
        if id(attachment) not in kept_ids
    ]
    source_label = "; ".join(source for source, _ in qc_emails)
    if not kept:
        return make_row(
            case_id,
            "skipped",
            source=source_label,
            removed=removed,
            note="no eligible document found in QC emails",
        )

    headers, body_text = merged_qc_content(qc_emails)
    if not headers.get("Subject"):
        headers["Subject"] = f"Consultation {case_id} (cleaned)"
    clean = build_clean_email(headers, kept, body_text)
    output_folder = OUTPUT_ROOT / case_id
    output_folder.mkdir(parents=True, exist_ok=True)
    (output_folder / f"{case_id} clean.eml").write_bytes(clean.as_bytes(policy=policy.default))

    return make_row(
        case_id,
        "ok",
        source=source_label,
        kept=[a.filename for a in kept],
        removed=removed,
        note="" if body_text else "no original message body could be identified",
    )


def write_log(rows: list[dict[str, str]]) -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("w", newline="", encoding="utf-8") as log_file:
        writer = csv.DictWriter(log_file, fieldnames=LOG_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def remove_non_idox(rows: list[dict[str, str]]) -> None:
    for case_id in NON_IDOX_CONSULTATIONS:
        output_folder = OUTPUT_ROOT / case_id
        if not output_folder.is_dir() or output_folder.is_symlink():
            continue
        shutil.rmtree(output_folder)
        matching_rows = [row for row in rows if row["consultation_id"] == case_id]
        if not matching_rows:
            rows.append(make_row(case_id, "skipped", note="removed: non-Idox website"))
        for row in matching_rows:
            row["status"] = "skipped"
            row["note"] = "; ".join(filter(None, (row["note"], "removed: non-Idox website")))


def main(remove_non_idox_enabled: bool = False) -> None:
    rows = []
    qc_groups: dict[str, list[tuple[str, EmailMessage]]] = {}
    for folder in sorted(path for path in INPUT_ROOT.iterdir() if path.is_dir() and not path.name.startswith(".")):
        try:
            folder_groups = collect_qc_groups(folder)
            if not folder_groups:
                rows.append(make_row(folder.name, "skipped", note="no QC email found"))
            for case_id, group in folder_groups.items():
                qc_groups.setdefault(case_id, []).extend(group)
        except Exception as error:
            # One bad folder should not stop the batch; the error is written to the log.
            rows.append(make_row(folder.name, "error", note=f"{type(error).__name__}: {error}"))

    rows.extend(
        process_qc_group(case_id, group)
        for case_id, group in sorted(qc_groups.items())
    )
    if remove_non_idox_enabled:
        remove_non_idox(rows)
    write_log(rows)
    counts = {
        status: sum(row["status"] == status for row in rows)
        for status in ("ok", "skipped", "error")
    }
    print(f"Processed {len(rows)} consultations: {counts}")
    print(f"Log: {LOG_PATH}")


if __name__ == "__main__":
    # Set to True to remove cleaned consultations with non-Idox websites
    remove_non_idox_enabled = True
    # Optional: input and output folders, e.g. data/temp/necessary data/temp/necessary_clean
    if len(sys.argv) == 3:
        INPUT_ROOT = Path(sys.argv[1]).resolve()
        OUTPUT_ROOT = Path(sys.argv[2]).resolve()
        LOG_PATH = OUTPUT_ROOT / "preprocessing_log.csv"
    main(remove_non_idox_enabled=remove_non_idox_enabled)
