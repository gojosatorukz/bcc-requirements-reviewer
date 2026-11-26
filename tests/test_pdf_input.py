from pathlib import Path
import tempfile
import unittest

from reviewer.input import load_document
from reviewer.segments import segment

try:
    import pdfplumber
    from reportlab.pdfgen.canvas import Canvas
except ImportError:
    Canvas = None


@unittest.skipUnless(Canvas, "PDF integration test requires pdfplumber and reportlab")
class PdfInputTest(unittest.TestCase):
    def test_ruled_table_and_source_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "example.pdf")
            pdf = Canvas(str(path), pagesize=(400, 400))
            pdf.drawString(30, 350, "API contract")
            pdf.grid([30, 160, 285], [180, 210, 240])
            for x, y, text in [(35,220,"Parameter"),(165,220,"Type"),(35,190,"amount"),(165,190,"integer")]:
                pdf.drawString(x, y, text)
            pdf.showPage()
            pdf.drawString(30, 350, "Result is OK.")
            pdf.save()
            lines, fingerprint, metadata = load_document(path)
        self.assertEqual(len(fingerprint), 64)
        self.assertEqual(metadata["page_count"], 2)
        rows = [b for b in segment(lines) if b["kind"] == "строка таблицы"]
        self.assertEqual(len(rows), 1)
        self.assertIn("| amount | integer |", lines)
        self.assertEqual(sum(line.count("amount") for line in lines), 1)
        self.assertEqual(lines[metadata["pages"][1]["start"] - 1], "Result is OK.")
