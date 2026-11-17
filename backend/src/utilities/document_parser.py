"""Extract PDF page text and render page images without OCR or model calls."""

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Union
import pypdfium2 as pdfium
from docx import Document
from docx.text.paragraph import Paragraph
from docx.table import Table
import shutil
import subprocess
from pathlib import Path


@dataclass(frozen=True)
class ParsedPage:
    source_path: Path
    page_number: int
    text: str
    image_path: Path


def parse_pdf(
    pdf_path: Union[str, Path],
    output_directory: Union[str, Path],
    *,
    dpi: int = 150,
) -> list[ParsedPage]:
    """Extract text and save one PNG per page, returning pages in source order.

    Pages without an embedded text layer have empty text but are still rendered.
    No OCR or layout reconstruction is performed. Existing images are never
    overwritten. PDF and filesystem errors propagate; images written before
    a later failure remain in the output directory.
    """

    if type(dpi) is not int or dpi <= 0:
        raise ValueError("dpi must be a positive integer.")
    source_path = Path(pdf_path).resolve()
    output_directory = Path(output_directory).resolve()
    pages = []

    with pdfium.PdfDocument(source_path) as document:
        document.init_forms()
        output_directory.mkdir(parents=True, exist_ok=True)
        for page_index in range(len(document)):
            page_number = page_index + 1
            image_path = output_directory / f"{source_path.stem}-page-{page_number:04d}.png"
            with closing(document[page_index]) as page:
                with closing(page.get_textpage()) as text_page:
                    text = text_page.get_text_bounded(errors="strict").replace("\r\n", "\n")
                with closing(page.render(scale=dpi / 72, draw_annots=True)) as bitmap:
                    with bitmap.to_pil() as image:
                        with image_path.open("xb") as output:
                            image.save(output, format="PNG")
            pages.append(ParsedPage(
                source_path=source_path,
                page_number=page_number,
                text=text,
                image_path=image_path,
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


def convert_docx_to_pdf_libreoffice(docx_path: Path, output_dir: Path) -> Path:
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
            str(docx_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )

    pdf_path = output_dir / f"{docx_path.stem}.pdf"
    if not pdf_path.is_file():
        raise FileNotFoundError(f"Conversion did not create {pdf_path}")

    return pdf_path