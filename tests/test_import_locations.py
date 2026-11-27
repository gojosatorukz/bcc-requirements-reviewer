from http import HTTPStatus
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from reviewer.serve import WorkbenchError, imported_full_report


WORD_REPORT = Path(__file__).resolve().parents[1] / "eval/word-transfer/runs/transfer.json"


class ImportLocationTests(TestCase):
    def test_saved_word_report_preserves_paragraph_and_table_addresses(self):
        raw = WORD_REPORT.read_bytes()
        report = json.loads(raw)
        name, html = imported_full_report(raw)
        self.assertEqual(name, report["run"]["input_name"])
        text = html.decode("utf-8")
        for location in report["run"]["locations"]:
            self.assertIn(location["label"], text)

    def test_invalid_word_addresses_are_rejected_before_rendering(self):
        report = json.loads(WORD_REPORT.read_bytes())
        count = report["run"]["input_lines"]
        first = {"start": 1, "end": 2, "label": "Абзац 1"}
        invalid = [
            {}, [None], [dict(first, start=True)], [dict(first, start=0)],
            [dict(first, end=count + 1)], [dict(first, start=3)],
            [first, dict(first, start=2)], [dict(first, label=17)], [dict(first, label=" ")],
        ]
        with patch("reviewer.full_render.render_full") as render:
            for locations in invalid:
                with self.subTest(locations=locations):
                    report["run"]["locations"] = locations
                    with self.assertRaises(WorkbenchError) as caught:
                        imported_full_report(json.dumps(report).encode())
                    self.assertEqual(caught.exception.code, HTTPStatus.UNPROCESSABLE_ENTITY)
            render.assert_not_called()
