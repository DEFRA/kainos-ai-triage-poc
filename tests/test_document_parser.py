import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pypdfium2 as pdfium
from PIL import Image, ImageDraw
from backend.src.utilities.document_parser import parse_doc, parse_pdf, parse_rtf


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
        # Parse a two-page PDF and render each page at a known resolution.
        # Check page order, extracted text, image properties and source preservation.
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
        # Render the PDF without specifying a resolution.
        # Check that the default DPI produces the expected image dimensions.
        pages = parse_pdf(self.pdf_path, self.output_directory)
        with Image.open(pages[0].image_path) as image:
            self.assertEqual(image.size, (300, 300))

    def test_image_only_page_is_rendered_without_inventing_text(self):
        # Parse a PDF containing an image but no text layer.
        # Check that the page is rendered and its extracted text is empty.
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
        # Render a PDF twice into the same output directory.
        # Check that the second attempt fails without modifying the first image.
        pages = parse_pdf(self.pdf_path, self.output_directory, dpi=72)
        original = pages[0].image_path.read_bytes()
        with self.assertRaises(FileExistsError):
            parse_pdf(self.pdf_path, self.output_directory, dpi=144)
        self.assertEqual(pages[0].image_path.read_bytes(), original)

    def test_documents_with_different_names_have_distinct_image_paths(self):
        # Render two identically sized PDFs with different filenames.
        # Check that their output image paths do not collide.
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
        # Try parsing a missing PDF and a file with invalid PDF contents.
        # Check that the respective file and PDF parsing errors propagate.
        with self.assertRaises(FileNotFoundError):
            parse_pdf(self.root / "missing.pdf", self.output_directory)
        invalid_path = self.root / "invalid.pdf"
        invalid_path.write_bytes(b"not a PDF")
        with self.assertRaises(pdfium.PdfiumError):
            parse_pdf(invalid_path, self.output_directory)

    def test_invalid_dpi_fails_before_creating_output(self):
        # Try invalid DPI values, including non-integer and boolean inputs.
        # Check that validation fails before creating the output directory.
        for dpi in (0, -72, True, 72.5):
            with self.subTest(dpi=dpi), self.assertRaisesRegex(ValueError, "dpi"):
                parse_pdf(self.pdf_path, self.output_directory, dpi=dpi)
        self.assertFalse(self.output_directory.exists())


class DocumentTextParserTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_doc_text_fallback_uses_antiword(self):
        # Extract text from a DOC file using a mocked antiword executable.
        # Check the returned text and the arguments passed to the command.
        with patch("backend.src.utilities.document_parser.shutil.which", return_value="/usr/bin/antiword"), \
             patch("backend.src.utilities.document_parser.subprocess.run") as run:
            run.return_value.stdout = "Planning details"
            self.assertEqual(parse_doc(self.root / "application.doc"), "Planning details")
            self.assertEqual(run.call_args.args[0], [
                "/usr/bin/antiword", "-m", "UTF-8.txt", str(self.root / "application.doc")
            ])

    def test_doc_text_fallback_requires_antiword(self):
        # Attempt DOC text extraction when antiword is unavailable.
        # Check that the missing executable is reported.
        with patch("backend.src.utilities.document_parser.shutil.which", return_value=None):
            with self.assertRaisesRegex(FileNotFoundError, "antiword"):
                parse_doc(self.root / "application.doc")

    def test_doc_text_fallback_reports_antiword_error(self):
        # Simulate antiword failing to extract a DOC file.
        # Check that its diagnostic is included in the raised error.
        error = subprocess.CalledProcessError(1, "antiword", stderr="Text stream too small")
        with patch("backend.src.utilities.document_parser.shutil.which", return_value="/usr/bin/antiword"), \
             patch("backend.src.utilities.document_parser.subprocess.run", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "Text stream too small"):
                parse_doc(self.root / "application.doc")

    def test_rtf_text_fallback_decodes_unicode_and_codepage(self):
        # Extract text containing RTF codepage and Unicode escape sequences.
        # Check that both characters are decoded correctly.
        path = self.root / "application.rtf"
        path.write_bytes(b"{\\rtf1\\ansi\\ansicpg1252 Caf\\'e9 and \\u8364?}")
        self.assertEqual(parse_rtf(path), "Caf\u00e9 and \u20ac")

    def test_rtf_text_fallback_rejects_non_rtf(self):
        # Attempt to parse a file without an RTF header.
        # Check that the invalid document is rejected.
        path = self.root / "application.rtf"
        path.write_bytes(b"not an RTF document")
        with self.assertRaisesRegex(ValueError, "Not an RTF"):
            parse_rtf(path)


if __name__ == "__main__":
    unittest.main()