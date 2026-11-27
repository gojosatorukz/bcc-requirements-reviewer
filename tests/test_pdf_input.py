from pathlib import Path
import tempfile
import unittest

from reviewer.input import load_document
from reviewer.executable import verify_source
from reviewer.json_examples import inspect_examples
from reviewer.segments import segment
import json

try:
    import pdfplumber
    from reportlab.pdfgen.canvas import Canvas
except ImportError:
    Canvas = None


@unittest.skipUnless(Canvas, "PDF integration test requires pdfplumber and reportlab")
class PdfInputTest(unittest.TestCase):
    def test_printed_api_objects_keep_real_line_numbers_and_a_failure_location(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "typed.pdf")
            pdf = Canvas(str(path))
            pdf.setFont("Courier", 11)
            rows = ['GET /{account}', '{placeholder}', '{"id": 1} is just prose', '{"ok": "braces } ["}',
                    '{', ' "items": [1, 2,],', ' "id": 3', '}', '["APPROVED", "REJECTED"]']
            for n, row in enumerate(rows):
                pdf.drawString(35, 740 - n * 18, row)
            pdf.setFont("Helvetica", 11)
            pdf.drawString(35, 490, '{"unformatted": 42}')
            pdf.save()
            lines, _, meta = load_document(path)
        native = segment(lines, meta["pdf_json_blocks"])
        checks = inspect_examples(lines, native)["examples"]
        self.assertEqual([(r["start"], r["end"], r["status"]) for r in checks],
                         [(4, 4, "valid"), (5, 8, "invalid"), (9, 9, "valid")])
        self.assertEqual(checks[1]["line"], 6)
        self.assertFalse(any("```" in text for text in lines))
        self.assertEqual(inspect_examples(lines, segment(lines))["examples"], [])

    def test_previous_pdf_report_still_verifies_without_printed_blocks_metadata(self):
        folder = Path(__file__).resolve().parents[1] / "eval/pdf-tariff"
        old = json.loads((folder / "review.json").read_text(encoding="utf-8"))
        self.assertNotIn("pdf_json_blocks", old["run"])
        self.assertEqual(verify_source(old, folder / "tariff.pdf"), old["run"]["input_sha256"])

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
