import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reviewer.__main__ import main


def example():
    return {"conflicts": [{
        "title": "Приоритет", "scenario": "Одновременно доступны A и B.",
        "first": {"source": {"line": 2, "quote": "Сначала A."}, "outcome": "A"},
        "second": {"source": {"line": 4, "quote": "Сначала B."}, "outcome": "B"},
        "question": "Какой приоритет выбрать?", "owner": "Владелец",
        "agreement_control": "Доступен только A.",
    }]}


class ReviewTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.doc = Path(temp.name, "input.md")
        self.doc.write_text("# Пример\nСначала A.\n\nСначала B.\n", encoding="utf-8")
        self.output = Path(temp.name, "report.json")

    def invoke(self, replies):
        with patch("sys.argv", ["reviewer", str(self.doc), str(self.output)]), \
                patch("reviewer.codex_provider.complete", side_effect=replies) as model, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            main()
        return model

    def test_repair_logs_cost_and_preserves_original_line_numbers(self):
        bad = deepcopy(example())
        bad["conflicts"][0]["first"]["source"]["line"] = 1
        model = self.invoke([(bad, {"input_tokens": 18}), (example(), {"input_tokens": 20})])
        report = json.loads(self.output.read_text())
        self.assertEqual([b["line"] for b in report["source"]], [1, 2, 4])
        self.assertEqual(report["run"]["usage"]["input_tokens"], 38)
        self.assertEqual([a["status"] for a in report["run"]["attempts"]], ["rejected", "accepted"])
        self.assertNotEqual(model.call_args_list[0].args[0], model.call_args_list[1].args[0])

    def test_failed_evidence_does_not_overwrite_previous_report(self):
        self.output.write_text("saved")
        bad = example()
        bad["conflicts"][0]["second"]["source"]["quote"] = "Выдуманная цитата."
        with self.assertRaises(SystemExit):
            self.invoke([(bad, {}), (bad, {})])
        self.assertEqual(self.output.read_text(), "saved")

    def test_empty_review_is_success_and_has_provenance(self):
        self.invoke([({"conflicts": []}, None)])
        report = json.loads(self.output.read_text())
        self.assertEqual(report["conflicts"], [])
        self.assertEqual(len(report["run"]["input_sha256"]), 64)
        self.assertIsNone(report["run"]["usage"])

    def test_provider_failure_is_not_clean_review(self):
        with self.assertRaises(SystemExit):
            self.invoke([ValueError("Провайдер недоступен")])
        self.assertFalse(self.output.exists())

    def test_one_command_writes_offline_html(self):
        self.output = self.output.with_suffix(".html")
        self.invoke([(example(), {})])
        html = self.output.read_text(encoding="utf-8")
        self.assertIn("Какой приоритет выбрать?", html)
        self.assertIn("Сначала B.", html)
