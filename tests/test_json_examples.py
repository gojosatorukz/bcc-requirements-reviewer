import json
from pathlib import Path
import unittest

from reviewer.full_render import render_json_checks, validate_report
from reviewer.json_examples import check_findings, inspect_examples
from reviewer.segments import segment


ROOT = Path(__file__).resolve().parents[1]


def inspect(lines):
    return inspect_examples(lines, segment(lines))


class JsonExampleTests(unittest.TestCase):
    def test_examples_follow_source_positions_and_ignore_other_languages(self):
        lines = ["", "~~~JSON", "{", "  ", '"a": 1,', "}", "~~~~", "~~~jsonc", "//", "~~~",
                 "~~~json", '["NaN", -Infinity]', "~~~", "~~~json", '["escaped \\"NaN\\"", 34]']
        a, b, c = inspect(lines)["examples"]
        self.assertEqual((a["start"], a["end"], a["line"], a["column"]), (2, 7, 6, 1))
        self.assertEqual((b["status"], b["line"], b["column"]), ("invalid", 12, 9))
        self.assertEqual((c["end"], c["status"]), (15, "valid"))
        self.assertEqual(inspect(["~~~json", "~~~"])["examples"][0]["status"], "invalid")
        html = render_json_checks({"examples": [a, b, c]}, dict(enumerate(lines, 1)))
        self.assertIn('#source-line-6', html)
        self.assertIn("Корректный JSON", html)
        blank_eof = inspect(["~~~json", "{", "", ""])
        html = render_json_checks(blank_eof, {1: "~~~json", 2: "{"})
        self.assertIn("позиция парсера, строка 4", html)
        self.assertNotIn("#source-line-4", html)

    def test_generated_values_and_trailing_comma_mutations(self):
        for n in range(80):
            value = {"n": n, "text": 'a "quote" & <' + str(n), "items": [None, True, n/10]}
            encoded = json.dumps(value, ensure_ascii=False)
            with self.subTest(n=n):
                self.assertEqual(inspect(["~~~json", encoded, "~~~"])["examples"][0]["status"], "valid")
                self.assertEqual(inspect(["~~~json", encoded[:-1] + ",}", "~~~"])["examples"][0]["status"], "invalid")
        for value in ["null", "false", "[]", '"NaN"', "2" * 6000, "1e9999"]:
            self.assertEqual(inspect(["~~~json", value, "~~~"])["examples"][0]["status"], "valid")

    def test_checked_sources_and_finding_gate(self):
        base = ROOT / "eval" / "blind-clearing"
        broken = inspect((base / "clearing.md").read_text().splitlines())
        fixed = inspect((base / "clearing-fixed.md").read_text().splitlines())
        self.assertEqual(broken["examples"][0]["line"], 28)
        self.assertEqual(fixed["examples"][0]["status"], "valid")
        finding = {"category": "нарушение", "title": "Синтаксис JSON", "reason": "", "action": "",
                   "evidence": [{"line": 27}]}
        check_findings(broken, [finding])
        finding["evidence"][0]["line"] = 7
        with self.assertRaisesRegex(ValueError, "цитировать"):
            check_findings(broken, [finding])
        check_findings(fixed, [])

    def test_old_official_report_and_tampered_syntax_summary(self):
        report = json.loads((ROOT / "runs/bcc-benchmark/review.json").read_text())
        lines = validate_report(report)
        raw = [lines.get(i, "") for i in range(1, report["run"]["input_lines"] + 1)]
        checks = inspect(raw)
        self.assertTrue(checks["examples"])
        self.assertTrue(all(e["status"] == "valid" for e in checks["examples"]))
        report["json_checks"] = checks
        validate_report(report)
        checks["examples"][0]["status"] = "invalid"
        with self.assertRaisesRegex(ValueError, "расходится"):
            validate_report(report)
