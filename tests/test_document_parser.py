import tempfile
import unittest
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw

from backend.src.utilities.document_parser import parse_pdf


class DocumentParserTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.pdf_path = self.root / "application.pdf"
        self.output_directory = self.root / "rendered"
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Count 2 /Kids [3 0 R 5 0 R] >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 144 144] "
            b"/Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>",
            b"",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 144 144] "
            b"/Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>",
            b"",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        for object_index, text in ((3, "Planning evidence"), (5, "Drainage findings")):
            stream = f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode("ascii")
            objects[object_index] = (
                f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
                + stream + b"\nendstream"
            )
        contents = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for object_number, body in enumerate(objects, start=1):
            offsets.append(len(contents))
            contents.extend(f"{object_number} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
        xref_offset = len(contents)
        contents.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
        for offset in offsets[1:]:
            contents.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
        contents.extend(
            f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
        )
        self.pdf_path.write_bytes(contents)

    def test_extracts_text_and_renders_all_pages_in_order(self):
        original = self.pdf_path.read_bytes()
        pages = parse_pdf(str(self.pdf_path), str(self.output_directory), dpi=72)

        self.assertEqual([page.page_number for page in pages], [1, 2])
        self.assertIn("Planning evidence", pages[0].text)
        self.assertIn("Drainage findings", pages[1].text)
        for page in pages:
            self.assertEqual(page.source_path, self.pdf_path.resolve())
            self.assertEqual(
                page.image_path.name, f"application-page-{page.page_number:04d}.png"
            )
            with Image.open(page.image_path) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, (144, 144))
                self.assertLess(image.convert("L").getextrema()[0], 255)
        self.assertEqual(self.pdf_path.read_bytes(), original)

    def test_default_dpi_controls_image_dimensions(self):
        pages = parse_pdf(self.pdf_path, self.output_directory)
        with Image.open(pages[0].image_path) as image:
            self.assertEqual(image.size, (300, 300))

    def test_image_only_page_is_rendered_without_inventing_text(self):
        scanned_path = self.root / "scanned.pdf"
        with Image.new("RGB", (144, 144), "white") as image:
            ImageDraw.Draw(image).rectangle((20, 20, 120, 120), fill="black")
            image.save(scanned_path, format="PDF", resolution=72)

        pages = parse_pdf(scanned_path, self.output_directory, dpi=72)

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].text, "")
        with Image.open(pages[0].image_path) as image:
            self.assertLess(image.convert("L").getextrema()[0], 255)

    def test_existing_render_is_not_overwritten(self):
        pages = parse_pdf(self.pdf_path, self.output_directory, dpi=72)
        original = pages[0].image_path.read_bytes()
        with self.assertRaises(FileExistsError):
            parse_pdf(self.pdf_path, self.output_directory, dpi=144)
        self.assertEqual(pages[0].image_path.read_bytes(), original)

    def test_documents_with_different_names_have_distinct_image_paths(self):
        first_pages = parse_pdf(self.pdf_path, self.output_directory, dpi=72)
        other_path = self.root / "other.pdf"
        other_path.write_bytes(self.pdf_path.read_bytes())
        other_pages = parse_pdf(other_path, self.output_directory, dpi=72)
        self.assertTrue(
            {page.image_path for page in first_pages}.isdisjoint(
                {page.image_path for page in other_pages}
            )
        )

    def test_missing_and_invalid_pdf_errors_propagate(self):
        with self.assertRaises(FileNotFoundError):
            parse_pdf(self.root / "missing.pdf", self.output_directory)
        invalid_path = self.root / "invalid.pdf"
        invalid_path.write_bytes(b"not a PDF")
        with self.assertRaises(pdfium.PdfiumError):
            parse_pdf(invalid_path, self.output_directory)

    def test_invalid_dpi_fails_before_creating_output(self):
        for dpi in (0, -72, True, 72.5):
            with self.subTest(dpi=dpi), self.assertRaisesRegex(ValueError, "dpi"):
                parse_pdf(self.pdf_path, self.output_directory, dpi=dpi)
        self.assertFalse(self.output_directory.exists())


if __name__ == "__main__":
    unittest.main()