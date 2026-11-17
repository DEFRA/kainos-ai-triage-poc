import base64
import copy
import hashlib
import quopri
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser, Parser
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Optional
from botocore.exceptions import ClientError
from extract_msg import openMsg
from extract_msg.msg_classes.msg import MSGFile
from backend.src.case_intake.tools.redact_emails import redact_emails
from backend.src.utilities.s3_utils import (
    put_object_with_metadata,
    upload_json_to_s3,
)


@dataclass
class QuotedChainSignal:
    has_quoted_chain: bool
    marker_count: int
    markers: list[str]


def iso_utc_now() -> str:
    """Return the current UTC time as an ISO-formatted string."""

    return datetime.now(timezone.utc).isoformat()


def safe_text(value: Any) -> str:
    """Convert a value to text, decoding bytes and treating None as empty."""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="ignore")
    return str(value)


def none_if_empty(value: Optional[str]) -> Optional[str]:
    """Strip whitespace and return None for empty values."""

    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def sha256_bytes(data: bytes) -> str:
    """Calculate the SHA-256 hash of bytes."""

    return hashlib.sha256(data).hexdigest()


def normalise_extension(filename: str) -> str:
    """Return a filename's extension in lowercase."""

    return Path(filename).suffix.lower()


def normalise_content_id(content_id: Optional[str]) -> Optional[str]:
    """Remove surrounding brackets and whitespace from a content ID."""

    if not content_id:
        return None
    return content_id.strip().lstrip("<").rstrip(">").strip() or None


def parse_references_header(raw: Optional[str]) -> list[str]:
    """Extract and deduplicate message IDs from a References header."""

    if not raw:
        return []

    tokens = re.findall(r"<[^>]+>", raw)
    if tokens:
        return list(dict.fromkeys(token.strip() for token in tokens if token.strip()))

    values = [value.strip() for value in re.split(r"[,\s]+", raw) if value.strip()]
    return list(dict.fromkeys(values))


def parse_datetime_iso_utc(datetime_raw: str) -> Optional[str]:
    """Parse a date and return it in UTC ISO format, or None."""

    if not datetime_raw:
        return None

    try:
        parsed = parsedate_to_datetime(datetime_raw)
    except Exception:
        try:
            parsed = datetime.fromisoformat(datetime_raw.replace("Z", "+00:00"))
        except Exception:
            return None

    # Treat timezone-free values as UTC and convert other values to UTC
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)

    return parsed.isoformat()


def detect_quoted_chain(body_text: str) -> QuotedChainSignal:
    """Identify quoted-reply markers in an email body."""

    if not body_text:
        return QuotedChainSignal(False, 0, [])

    # Look for common reply markers in the email body
    markers: list[str] = []

    if re.search(r"(?im)^from:\s.+$", body_text):
        has_sent = bool(re.search(r"(?im)^sent:\s.+$", body_text))
        has_to = bool(re.search(r"(?im)^to:\s.+$", body_text))
        has_subject = bool(re.search(r"(?im)^subject:\s.+$", body_text))
        if has_sent and (has_to or has_subject):
            markers.append("outlook_from_sent_to_subject_block")

    if re.search(r"(?im)^on .+ wrote:\s*$", body_text):
        markers.append("on_wrote_block")

    if re.search(r"(?im)^[- ]{2,}original message[- ]{2,}\s*$", body_text):
        markers.append("original_message_separator")

    header_lines = re.findall(
        r"(?im)^(from|sent|to|subject):\s.+$",
        body_text,
    )
    if len(header_lines) >= 3:
        markers.append("multiple_header_like_lines_in_body")

    return QuotedChainSignal(bool(markers), len(markers), markers)


def classify_email_category(
    body_text: str,
    references: list[str],
) -> tuple[str, list[str]]:
    """Classify an email as a chain or standalone and explain why."""

    reasons: list[str] = []
    quoted_signal = detect_quoted_chain(body_text)

    if references:
        reasons.append("References header contains prior message identifiers.")

    if quoted_signal.marker_count >= 2:
        reasons.append("Multiple quoted-reply markers found in the body.")

    if reasons:
        return "chain", reasons

    return "standalone", ["No strong chain indicators detected."]


def derive_email_parse_status(
    parse_errors: list[str],
    attachments: list[dict[str, Any]],
    hard_error: bool = False,
) -> str:
    """Determine whether parsing succeeded, was partial, or failed."""

    if hard_error:
        return "error"

    if parse_errors or any(
        attachment.get("parse_status") in {"partial", "error"}
        for attachment in attachments
    ):
        return "partial"

    return "ok"


def extract_msg_header_value(msg: Any, header_name: str) -> Optional[str]:
    """Retrieve a named header from an Outlook message."""

    # Extract MSG headers from either a Message or a raw string
    try:
        header = getattr(msg, "header", None)

        if isinstance(header, Message):
            parsed = header
        elif isinstance(header, str) and header.strip():
            parsed = Parser(policy=policy.default).parsestr(header)
        else:
            return None

        return none_if_empty(safe_text(parsed.get(header_name)))
    except Exception:
        return None


def build_attachment_record(
    index: int,
    filename: str,
    mime_type: str,
    content_id: Optional[str],
    size_bytes: Optional[int],
    is_inline: Optional[bool],
    sha256: Optional[str],
    parse_status: str,
    parse_error: list[str],
) -> dict[str, Any]:
    """Create a standard attachment metadata record."""

    return {
        "index": index,
        "filename": filename,
        "mime_type": mime_type,
        "content_id": content_id,
        "size_bytes": size_bytes,
        "is_inline": is_inline,
        "sha256": sha256,
        "s3_uri": None,
        "s3_structured": [],
        "parse_status": parse_status,
        "parse_error": parse_error,
    }


# Retain encoded attached emails as bytes during parsing
class EmailPartForParsing(EmailMessage):
    _parsing = True

    def get_content_maintype(self):
        """Preserve encoded attached emails during MIME parsing."""

        content_type = self.get_content_type()
        encoding = str(self.get("Content-Transfer-Encoding", "")).strip().lower()

        if (
            self._parsing
            and content_type == "message/rfc822"
            and encoding in {"base64", "quoted-printable"}
        ):
            return "application"

        return super().get_content_maintype()


def parse_eml_bytes(raw_bytes: bytes) -> EmailMessage:
    """Parse EML bytes while preserving encoded attached emails."""

    parser_policy = policy.default.clone(message_factory=EmailPartForParsing)
    message = BytesParser(policy=parser_policy).parsebytes(raw_bytes)

    for part in message.walk():
        part._parsing = False

    return message


def eml_attachment_bytes(part: Any) -> bytes:
    """Recover and decode an EML attachment's bytes."""

    mime_type = part.get_content_type()
    encoding = safe_text(part.get("Content-Transfer-Encoding")).strip().lower()

    if mime_type == "message/rfc822" and encoding in {
        "base64",
        "quoted-printable",
    }:
        encoded = part.get_payload(decode=False)
        if not isinstance(encoded, str):
            raise ValueError("Encoded email payload could not be recovered.")

        encoded_bytes = encoded.encode("ascii")

        if encoding == "base64":
            return base64.b64decode(
                re.sub(rb"\s+", b"", encoded_bytes),
                validate=True,
            )

        if re.search(rb"=(?![0-9A-Fa-f]{2}|\r?\n)", encoded_bytes):
            raise ValueError("Invalid quoted-printable email attachment.")

        return quopri.decodestring(encoded_bytes)

    payload = part.get_payload(decode=True)
    if payload is not None:
        return payload

    # Recover an unencoded attached email from its nested message
    if mime_type == "message/rfc822":
        nested = part.get_payload()
        if isinstance(nested, list) and len(nested) == 1:
            return nested[0].as_bytes(policy=policy.default)

    raise ValueError("Attachment bytes are unavailable.")


def iter_eml_attachment_parts(msg: Any):
    """Yield attachment parts without traversing into attached emails."""

    # Traverse MIME containers without entering attached emails
    def visit(part: Any, *, is_root: bool = False):
        """Recursively traverse MIME parts to identify attachments."""

        mime_type = part.get_content_type()
        maintype = part.get_content_maintype()
        disposition = part.get_content_disposition()

        if not is_root and (
            disposition == "attachment"
            or part.get_filename()
            or mime_type == "message/rfc822"
        ):
            yield part
            return

        if maintype == "multipart":
            for child in part.iter_parts():
                yield from visit(child)
            return

        if not is_root and (
            mime_type not in {"text/plain", "text/html"}
            or part.get("Content-ID")
        ):
            yield part

    yield from visit(msg, is_root=True)


def parse_eml_attachments(
    msg: Any,
) -> tuple[list[dict[str, Any]], list[Optional[bytes]]]:
    """Extract attachment metadata and bytes from an EML message."""

    attachments: list[dict[str, Any]] = []
    blobs: list[Optional[bytes]] = []

    for index, part in enumerate(iter_eml_attachment_parts(msg)):
        filename = part.get_filename() or f"attachment_{index}"
        mime_type = part.get_content_type() or "application/octet-stream"

        # Give attached emails an extension that bypasses document processing
        if mime_type == "message/rfc822" and Path(filename).suffix.lower() != ".eml":
            filename += ".eml"

        content_id = normalise_content_id(safe_text(part.get("Content-ID")))
        disposition = part.get_content_disposition()

        if disposition == "inline":
            is_inline: Optional[bool] = True
        elif disposition == "attachment":
            is_inline = False
        elif content_id:
            is_inline = True
        else:
            is_inline = None

        blob: Optional[bytes] = None
        size_bytes: Optional[int] = None
        sha256: Optional[str] = None
        parse_status = "ok"
        parse_error: list[str] = []

        # Recover attachment bytes and record partial results when recovery fails
        try:
            blob = eml_attachment_bytes(part)
            size_bytes = len(blob)
            sha256 = sha256_bytes(blob)
        except Exception:
            parse_status = "partial"
            parse_error.append("Attachment bytes could not be recovered.")

        attachments.append(
            build_attachment_record(
                index=index,
                filename=filename,
                mime_type=mime_type,
                content_id=content_id,
                size_bytes=size_bytes,
                is_inline=is_inline,
                sha256=sha256,
                parse_status=parse_status,
                parse_error=parse_error,
            )
        )
        blobs.append(blob)

    return attachments, blobs


def parse_msg_attachments(
    msg: Any,
) -> tuple[list[dict[str, Any]], list[Optional[bytes]]]:
    """Extract attachment metadata and bytes from an Outlook message."""

    attachments: list[dict[str, Any]] = []
    blobs: list[Optional[bytes]] = []

    for index, attachment in enumerate(getattr(msg, "attachments", []) or []):
        filename = (
            none_if_empty(safe_text(getattr(attachment, "longFilename", None)))
            or none_if_empty(safe_text(getattr(attachment, "filename", None)))
            or f"attachment_{index}"
        )
        mime_type = (
            none_if_empty(safe_text(getattr(attachment, "mimetype", None)))
            or "application/octet-stream"
        )

        if mime_type == "message/rfc822" and Path(filename).suffix.lower() != ".eml":
            filename += ".eml"
        elif (
            mime_type == "application/vnd.ms-outlook"
            and Path(filename).suffix.lower() != ".msg"
        ):
            filename += ".msg"

        content_id = normalise_content_id(
            none_if_empty(safe_text(getattr(attachment, "cid", None)))
            or none_if_empty(safe_text(getattr(attachment, "contentId", None)))
        )
        is_inline: Optional[bool] = True if content_id else None

        blob: Optional[bytes] = None
        size_bytes: Optional[int] = None
        sha256: Optional[str] = None
        parse_status = "ok"
        parse_error: list[str] = []

        # Export embedded messages while the parent MSG file is still open
        try:
            data = getattr(attachment, "data", None)
            if callable(data):
                data = data()

            if isinstance(data, (bytes, bytearray)):
                blob = bytes(data)
            elif isinstance(data, MSGFile):
                blob = data.exportBytes()
                mime_type = "application/vnd.ms-outlook"
                if Path(filename).suffix.lower() != ".msg":
                    filename += ".msg"
            else:
                raise ValueError("Unsupported attachment data.")

            size_bytes = len(blob)
            sha256 = sha256_bytes(blob)
        except Exception:
            parse_status = "partial"
            parse_error.append("Attachment bytes could not be recovered.")

        attachments.append(
            build_attachment_record(
                index=index,
                filename=filename,
                mime_type=mime_type,
                content_id=content_id,
                size_bytes=size_bytes,
                is_inline=is_inline,
                sha256=sha256,
                parse_status=parse_status,
                parse_error=parse_error,
            )
        )
        blobs.append(blob)

    return attachments, blobs


def parse_email_file(
    email_file_path: str | Path,
) -> tuple[dict[str, Any], bytes, list[Optional[bytes]]]:
    """Parse one EML or MSG file and return metadata, raw bytes and attachment bytes."""

    path = Path(email_file_path)
    extension = path.suffix.lower()

    if extension not in {".eml", ".msg"}:
        raise ValueError(f"Unsupported file extension: {extension}")

    raw_bytes = path.read_bytes()
    parse_errors: list[str] = []

    subject = ""
    datetime_raw = ""
    message_id: Optional[str] = None
    references: list[str] = []
    body_text = ""
    body_html: Optional[str] = None

    # Parse the email headers, body and attachments according to its file type
    if extension == ".eml":
        msg = parse_eml_bytes(raw_bytes)

        subject = safe_text(msg.get("Subject"))
        datetime_raw = safe_text(msg.get("Date"))
        message_id = none_if_empty(safe_text(msg.get("Message-ID")))
        references = parse_references_header(safe_text(msg.get("References")))

        # Select the parent email body without entering attached messages
        plain_part = msg.get_body(preferencelist=("plain",))
        if plain_part is not None:
            body_text = safe_text(plain_part.get_content())

        html_part = msg.get_body(preferencelist=("html",))
        if html_part is not None:
            html = safe_text(html_part.get_content())
            body_html = html if html.strip() else None

        attachments, attachment_blobs = parse_eml_attachments(msg)

    else:
        msg = None
        try:
            try:
                from extract_msg.enums import ErrorBehavior

                msg = openMsg(
                    str(path),
                    strict=False,
                    delayAttachments=True,
                    errorBehavior=(
                        ErrorBehavior.ATTACH_SUPPRESS_ALL
                        | ErrorBehavior.STANDARDS_VIOLATION
                        | ErrorBehavior.OLE_DEFECT_INCORRECT
                        | ErrorBehavior.RTFDE
                        | ErrorBehavior.NAMED_NAME_STREAM
                    ),
                )
            except Exception:
                msg = openMsg(str(path), strict=False, delayAttachments=True)

            subject = safe_text(getattr(msg, "subject", ""))
            date_value = getattr(msg, "date", "")
            datetime_raw = (
                date_value.isoformat()
                if isinstance(date_value, datetime)
                else safe_text(date_value)
            )
            message_id = (
                none_if_empty(safe_text(getattr(msg, "messageId", None)))
                or extract_msg_header_value(msg, "Message-ID")
            )
            references = parse_references_header(
                extract_msg_header_value(msg, "References")
            )
            body_text = safe_text(getattr(msg, "body", ""))

            html = getattr(msg, "htmlBody", None)
            if isinstance(html, (bytes, bytearray)):
                html = bytes(html).decode("utf-8", errors="ignore")
            body_html = html if isinstance(html, str) and html.strip() else None

            attachments, attachment_blobs = parse_msg_attachments(msg)
        finally:
            close_method = getattr(msg, "close", None)
            if callable(close_method):
                close_method()

    # Classify the email and assemble its metadata and attachment details
    category, reasons = classify_email_category(body_text, references)
    attachment_extensions = sorted(
        {
            extension
            for extension in (
                normalise_extension(attachment["filename"])
                for attachment in attachments
            )
            if extension
        }
    )

    payload = {
        "metadata": {
            "extracted_at_iso_utc": iso_utc_now(),
            "parse_status": derive_email_parse_status(parse_errors, attachments),
            "parse_errors": parse_errors,
        },
        "file": {
            "filename": path.name,
            "extension": extension,
            "size_bytes": len(raw_bytes),
            "sha256": sha256_bytes(raw_bytes),
        },
        "headers": {
            "subject": subject,
            "datetime_raw": datetime_raw,
            "datetime_iso_utc": parse_datetime_iso_utc(datetime_raw),
            "message_id": message_id,
            "references": references,
        },
        "body": {"text": body_text, "html": body_html},
        "attachments": attachments,
        "attachments_count": len(attachments),
        "attachments_extensions": attachment_extensions,
        "classification": {
            "email_category": category,
            "email_category_reasons": reasons,
        },
    }

    return payload, raw_bytes, attachment_blobs


def clean_path_segment(value: Optional[str], fallback: str) -> str:
    """Replace separators and whitespace in a path segment, using a fallback when empty."""

    if value is None or not str(value).strip():
        return fallback
    cleaned = re.sub(r"[\\/]+", "_", str(value).strip())
    return re.sub(r"\s+", "_", cleaned)


def prefix_has_objects(s3_client: Any, bucket: str, prefix: str) -> bool:
    """Return whether an S3 folder prefix contains any objects."""

    response = s3_client.list_objects_v2(
        Bucket=bucket,
        Prefix=f"{prefix}/",
        MaxKeys=1,
    )
    return bool(response.get("KeyCount", 0))


def resolve_unique_email_prefix(
    s3_client: Any,
    bucket: str,
    folder_prefix: str,
    consultation_id: str,
    file_folder_name: str,
) -> str:
    """Reserve and return an unused email folder prefix using an S3 marker."""

    base = f"{folder_prefix}/{consultation_id}/{file_folder_name}"
    version = 1

    for _ in range(10_000):
        candidate = base if version == 1 else f"{base}_v{version}"

        # Preserve compatibility with folders created before reservations
        if prefix_has_objects(s3_client, bucket, candidate):
            version += 1
            continue

        try:
            # All concurrent writers must reserve and retain this marker
            s3_client.put_object(
                Bucket=bucket,
                Key=f"{candidate}/.reservation",
                Body=b"",
                IfNoneMatch="*",
            )
            return candidate
        except ClientError as exc:
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = exc.response.get("Error", {}).get("Code")

            if status == 412 or code == "PreconditionFailed":
                version += 1
                continue

            if status == 409 or code == "ConditionalRequestConflict":
                continue

            raise

    raise RuntimeError("Could not reserve a unique email prefix.")


def upload_raw_email_to_s3(
    s3_client: Any,
    bucket: str,
    prefix: str,
    original_filename: str,
    raw_bytes: bytes,
) -> dict[str, Any]:
    """Upload an original email and return its S3 location and version metadata."""

    return put_object_with_metadata(
        s3_client,
        bucket,
        f"{prefix}/{original_filename}",
        raw_bytes,
    )


def unique_attachment_name(filename: str, seen: set[str]) -> str:
    """Return a sanitized unique attachment name and add it to the seen set."""

    name = filename.strip() if filename and filename.strip() else "attachment"
    name = name.replace("/", "_").replace("\\", "_")
    candidate = name
    version = 2

    while candidate in seen:
        candidate = f"{Path(name).stem}_{version}{Path(name).suffix}"
        version += 1

    seen.add(candidate)
    return candidate


def upload_attachments_to_s3(
    s3_client: Any,
    bucket: str,
    prefix: str,
    attachments: list[dict[str, Any]],
    attachment_blobs: list[Optional[bytes]],
    parse_errors: list[str],
) -> None:
    """Upload attachments and update their records and parse errors in place."""

    seen_names: set[str] = set()

    for index, attachment in enumerate(attachments):
        blob = attachment_blobs[index] if index < len(attachment_blobs) else None

        # None means unavailable while empty bytes are a valid attachment
        if blob is None:
            if attachment.get("parse_status") == "ok":
                attachment["parse_status"] = "partial"
            attachment["parse_error"].append(
                "Attachment was not uploaded because bytes are unavailable."
            )
            continue

        name = unique_attachment_name(attachment["filename"], seen_names)
        key = f"{prefix}/attachments/{name}"

        try:
            storage = put_object_with_metadata(s3_client, bucket, key, blob)
            attachment["s3_uri"] = storage["s3_uri"]
            attachment["s3_structured"] = [{
                "bucket": storage["bucket"],
                "key": storage["key"],
                "version": storage["version"],
            }]
        except Exception:
            if attachment.get("parse_status") == "ok":
                attachment["parse_status"] = "partial"
            message = "Attachment upload failed."
            attachment["parse_error"].append(message)
            parse_errors.append(message)


def parse_email(
    email_file_path: str | Path,
    s3_client: Any,
    bucket: str,
    consultation_id: Optional[str] = None,
    folder_prefix: Optional[str] = None,
) -> dict[str, Any]:
    """Upload redacted email metadata, the original email, and its attachments."""

    if not bucket or not bucket.strip():
        raise ValueError("bucket is mandatory.")
    if s3_client is None:
        raise ValueError("s3_client is mandatory.")

    path = Path(email_file_path)

    # Parse the email and return an error record if parsing fails
    try:
        payload, raw_bytes, attachment_blobs = parse_email_file(path)
    except Exception:
        return {
            "s3_key": None,
            "json": {
                "metadata": {
                    "extracted_at_iso_utc": iso_utc_now(),
                    "parse_status": "error",
                    "parse_errors": ["Email could not be parsed."],
                },
                "file": {
                    "filename": path.name,
                    "extension": path.suffix.lower(),
                    "size_bytes": None,
                    "sha256": None,
                },
                "headers": {
                    "subject": "",
                    "datetime_raw": "",
                    "datetime_iso_utc": None,
                    "message_id": None,
                    "references": [],
                },
                "body": {"text": "", "html": None},
                "attachments": [],
                "attachments_count": 0,
                "attachments_extensions": [],
                "classification": {
                    "email_category": "standalone",
                    "email_category_reasons": [
                        "Email parsing failed before classification."
                    ],
                },
            },
        }

    # Stop before uploading if email address redaction fails
    try:
        redacted_payload = redact_emails(copy.deepcopy(payload))
        if not isinstance(redacted_payload, dict):
            raise TypeError("Redaction did not return a dictionary.")
    except Exception:
        return {
            "s3_key": None,
            "json": {
                "metadata": {
                    "extracted_at_iso_utc": iso_utc_now(),
                    "parse_status": "error",
                    "parse_errors": ["Redaction failed; no data was uploaded."],
                }
            }
        }

    parse_errors = redacted_payload["metadata"]["parse_errors"]

    # Build the S3 folder names from the supplied location and redacted filename
    prefix_parts = (
        folder_prefix.strip("/").split("/")
        if folder_prefix and folder_prefix.strip("/")
        else ["unknown_category"]
    )
    folder_segment = "/".join(
        clean_path_segment(part, "unknown_category")
        for part in prefix_parts
    )
    consultation_segment = clean_path_segment(
        consultation_id,
        "unknown_consultation_id",
    )
    file_segment = clean_path_segment(
        Path(redacted_payload["file"]["filename"]).stem,
        "email",
    )

    # Reserve a unique email folder using an S3 marker object
    try:
        resolved_prefix = resolve_unique_email_prefix(
            s3_client=s3_client,
            bucket=bucket,
            folder_prefix=folder_segment,
            consultation_id=consultation_segment,
            file_folder_name=file_segment,
        )
    except Exception as error:
        parse_errors.append(f"S3 folder reservation failed: {error}")
        redacted_payload["metadata"]["parse_status"] = derive_email_parse_status(
            parse_errors,
            redacted_payload["attachments"],
        )
        return {
            "s3_key": None,
            "json": redacted_payload,
        }

    redacted_payload["storage"] = {
        "bucket": bucket,
        "prefix": resolved_prefix,
    }

    # Upload the original email and its unredacted attachments
    try:
        redacted_payload["storage"]["raw_email"] = upload_raw_email_to_s3(
            s3_client=s3_client,
            bucket=bucket,
            prefix=resolved_prefix,
            original_filename=redacted_payload["file"]["filename"],
            raw_bytes=raw_bytes,
        )
    except Exception:
        parse_errors.append("Raw email upload failed.")

    upload_attachments_to_s3(
        s3_client=s3_client,
        bucket=bucket,
        prefix=resolved_prefix,
        attachments=redacted_payload["attachments"],
        attachment_blobs=attachment_blobs,
        parse_errors=parse_errors,
    )

    # Record the final status and metadata key before uploading the JSON
    redacted_payload["metadata"]["parse_status"] = derive_email_parse_status(
        parse_errors,
        redacted_payload["attachments"],
    )

    json_key = f"{resolved_prefix}/email_metadata.json"
    redacted_payload["storage"]["json"] = {
        "bucket": bucket,
        "key": json_key,
        "version": None,
        "s3_uri": f"s3://{bucket}/{json_key}",
    }

    # Upload the redacted metadata and report any upload failure
    try:
        upload_json_to_s3(
            s3_client=s3_client,
            bucket=bucket,
            prefix=resolved_prefix,
            payload=redacted_payload,
            json_name="email_metadata.json",
        )
    except Exception:
        redacted_payload["storage"].pop("json", None)
        parse_errors.append("JSON upload failed.")
        redacted_payload["metadata"]["parse_status"] = derive_email_parse_status(
            parse_errors,
            redacted_payload["attachments"],
        )

    return {
        "s3_key": redacted_payload.get("storage", {}).get("json", {}).get("key"),
        "json": redacted_payload,
    }