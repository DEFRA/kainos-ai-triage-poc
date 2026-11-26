from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional, Union
import pypdfium2 as pdfium
from docx import Document
from docx.text.paragraph import Paragraph
from docx.table import Table
import shutil
import subprocess
import re
from pathlib import Path
from zipfile import BadZipFile
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from striprtf.striprtf import rtf_to_text


@dataclass(frozen=True)
class ParsedPage:
    source_path: Path
    page_number: int
    text: str
    image_path: Optional[Path]
    has_visual_content: bool = False


def parse_pdf(
    pdf_path: Union[str, Path],
    output_directory: Union[str, Path],
    *,
    dpi: int = 150,
    image_format: Literal["png", "jpeg"] = "png",
    jpeg_quality: int = 90,
    render_images: bool = True,
) -> list[ParsedPage]:
    """Extract text and render pages, returning them in source order.

    Pages without an embedded text layer have empty text but are still rendered.
    ``has_visual_content`` indicates whether PDFium found any non-text page
    objects. No OCR or layout reconstruction is performed. Existing images are
    never overwritten. PDF and filesystem errors propagate; images written
    before a later failure remain in the output directory.
    """

    if type(dpi) is not int or dpi <= 0:
        raise ValueError("dpi must be a positive integer.")
    if image_format not in {"png", "jpeg"}:
        raise ValueError("image_format must be 'png' or 'jpeg'.")
    if type(jpeg_quality) is not int or not 1 <= jpeg_quality <= 95:
        raise ValueError("jpeg_quality must be an integer between 1 and 95.")
    if type(render_images) is not bool:
        raise ValueError("render_images must be true or false.")
    source_path = Path(pdf_path).resolve()
    output_directory = Path(output_directory).resolve()
    pages = []

    with pdfium.PdfDocument(source_path) as document:
        document.init_forms()
        if render_images:
            output_directory.mkdir(parents=True, exist_ok=True)
        for page_index in range(len(document)):
            page_number = page_index + 1
            suffix = ".jpg" if image_format == "jpeg" else ".png"
            image_path = output_directory / f"{source_path.stem}-page-{page_number:04d}{suffix}"
            with closing(document[page_index]) as page:
                with closing(page.get_textpage()) as text_page:
                    text = text_page.get_text_bounded(errors="strict").replace("\r\n", "\n")
                has_visual_content = any(
                    page_object.type != pdfium.raw.FPDF_PAGEOBJ_TEXT
                    for page_object in page.get_objects()
                )
                if render_images:
                    with closing(page.render(scale=dpi / 72, draw_annots=True)) as bitmap:
                        with bitmap.to_pil() as image:
                            with image_path.open("xb") as output:
                                if image_format == "jpeg":
                                    image.convert("RGB").save(
                                        output, format="JPEG", quality=jpeg_quality, optimize=True,
                                    )
                                else:
                                    image.save(output, format="PNG")
            pages.append(ParsedPage(
                source_path=source_path,
                page_number=page_number,
                text=text,
                image_path=image_path if render_images else None,
                has_visual_content=has_visual_content,
            ))

    return pages


def parse_docx(docx_path: Union[str, Path]) -> str:
    """Extract (only) text from document paragraphs and tables."""

    document = Document(docx_path)

    def extract_text(container):
        for block in container.iter_inner_content():
            if isinstance(block, Paragraph):
                yield block.text
            elif isinstance(block, Table):
                for row in block.rows:
                    for cell in row.cells:
                        yield from extract_text(cell)

    return "\n".join(extract_text(document))


def parse_xlsx(xlsx_path: Union[str, Path]) -> list[dict[str, object]]:
    """Extract non-empty cells as text grouped by worksheet and cell address."""
    source_path = Path(xlsx_path)
    try:
        workbook = load_workbook(source_path, read_only=True, data_only=False)
    except (OSError, BadZipFile, InvalidFileException, KeyError) as error:
        raise ValueError(f"Could not read XLSX workbook {source_path}: {error}") from error

    sheets = []
    has_cells = False
    with closing(workbook):
        for worksheet in workbook.worksheets:
            cells = [
                {"address": cell.coordinate, "value": str(cell.value)}
                for row in worksheet.iter_rows()
                for cell in row
                if cell.value is not None
            ]
            has_cells = has_cells or bool(cells)
            sheets.append({"name": worksheet.title, "cells": cells})

    if not has_cells:
        raise ValueError(f"XLSX workbook contains no non-empty cells: {source_path}")
    return sheets


def parse_doc(doc_path: Union[str, Path]) -> str:
    """Extract text from a legacy Word document using antiword.

    This is a text-only fallback when DOC-to-PDF conversion fails. Unlike DOCX,
    DOC is a binary format whose text may be distributed across document
    structures, so python-docx cannot extract it. Some Python alternatives
    rely on external converters; others, such as Aspose.Words, are commercial.
    Antiword provides a free fallback independent of LibreOffice, but it may
    reject some documents, including files with very small text streams.

    macOS developers can install it locally with `brew install antiword`;
    Homebrew marks the formula as deprecated. The Docker image installs it
    separately. Antiword must be available on PATH.
    """

    antiword = shutil.which("antiword")
    if not antiword:
        raise FileNotFoundError("antiword was not found on PATH.")

    try:
        return subprocess.run(
            [antiword, "-m", "UTF-8.txt", str(doc_path)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        ).stdout
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"antiword failed to read {doc_path}: {error.stderr.strip()}") from error


def parse_rtf(rtf_path: Union[str, Path]) -> str:
    """Extract text from an RTF document using its declared code page."""

    data = Path(rtf_path).read_bytes()
    if not data.lstrip().startswith(b"{\\rtf"):
        raise ValueError(f"Not an RTF document: {rtf_path}")
    codepage = re.search(rb"\\ansicpg(\d+)", data)
    encoding = f"cp{codepage.group(1).decode('ascii')}" if codepage else "cp1252"
    return rtf_to_text(data.decode(encoding), encoding=encoding)


def convert_document_to_pdf_libreoffice(document_path: Path, output_dir: Path) -> Path:
    """Convert file to PDF using LibreOffice in headless mode."""

    soffice = shutil.which("soffice")
    if not soffice:
        raise FileNotFoundError("LibreOffice soffice was not found on PATH.")

    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = output_dir / "libreoffice_profile"

    subprocess.run(
        [
            soffice,
            f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
            "--headless",
            "--convert-to", "pdf",
            "--outdir", str(output_dir),
            str(document_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )

    pdf_path = output_dir / f"{document_path.stem}.pdf"
    if not pdf_path.is_file():
        raise FileNotFoundError(f"Conversion did not create {pdf_path}")

    return pdf_path