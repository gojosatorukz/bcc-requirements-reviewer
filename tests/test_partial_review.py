import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from reviewer import __main__ as cli
from reviewer.gemini_provider import QuotaExceeded
from reviewer.input import load_document
from reviewer.partial import build_partial
from reviewer.render import render_report
from reviewer.serve import WorkbenchError, imported_full_report

CASES = Path(__file__).resolve().parents[1] / "eval/quota-review"


class PartialReviewTests(TestCase):
    def test_cli_quota_only_for_requested_fallback(self):
        for enabled, error, expected in [(True, QuotaExceeded("Gemini API: HTTP 429"), True),
                                        (False, QuotaExceeded("Gemini API: HTTP 429"), False),
                                        (True, ValueError("Gemini API: HTTP 429"), False)]:
            with self.subTest(enabled=enabled, error=type(error)), TemporaryDirectory() as directory:
                out = Path(directory) / "review.json"
                source = CASES / "reservations.md"
                args = ["reviewer", str(source), str(out), "--model", "gemini-test"] + (["--partial-on-quota"] if enabled else [])
                with patch.object(sys, "argv", args), patch.dict("os.environ", {"REVIEW_PROVIDER":"gemini", "REVIEW_FALLBACK_MODEL":""}), \
                     patch("reviewer.full.analyze", side_effect=error):
                    if expected:
                        cli.main()
                    else:
                        with self.assertRaises(SystemExit):
                            cli.main()
                self.assertEqual(out.exists(), expected)
                if expected:
                    report = json.loads(out.read_text())
                    self.assertEqual(report["input_sha256"], load_document(source)[1])
                    self.assertEqual([[e["line"] for e in f["evidence"]] for f in report["findings"]], [[7, 8], [13], [18]])
                    with self.assertRaises(WorkbenchError):
                        imported_full_report(out.read_bytes())

    def test_control_and_cited_evidence(self):
        for name, expected in [("reservations.md", 3), ("reservations-fixed.md", 0)]:
            lines, digest, metadata = load_document(CASES / name)
            report = build_partial(name, lines, digest, metadata, "gemini-test")
            self.assertEqual(report["total_findings"], expected)
            for finding in report["findings"]:
                self.assertTrue(finding["question"].endswith("?"))
                for e in finding["evidence"]:
                    self.assertEqual(e["quote"], lines[e["line"]-1])
            self.assertIn("Частичный отчёт", render_report(report))

    def test_only_comparable_table_rows_and_explicit_prose(self):
        doc = ["| Mode | Status |", "| --- | --- |", "| open | ready |", "| open | ready |",
               "", "| Mode | Status |", "| --- | --- |", "| open | queued |", "",
               "~~~text", "Time: TBD.", "~~~", "Датчик должен быстро подать сигнал.", "Период: TBD.",
               "## С таймером", "Датчик должен быстро подать сигнал.", "Время до ответа — 15 мс."]
        report = build_partial("<report>", doc, "0"*64, {}, "gemini-test")
        self.assertEqual([f["rule"] for f in report["findings"]], ["unmeasured_time","missing_parameter"])
        self.assertNotIn("<report>", render_report(report))
        self.assertIn("&lt;report&gt;", render_report(report))
