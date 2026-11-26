"""Check guided evidence markup for PDF and Markdown reports."""

import json
from pathlib import Path
import shutil
import subprocess
import unittest
from xml.etree import ElementTree

GUIDED = Path(__file__).resolve().parents[1] / "reviewer" / "guided.js"
HARNESS = """
const fs = require('node:fs');
const vm = require('node:vm');
const root = {
  querySelector() { return null; },
  querySelectorAll() { return []; },
};
const sandbox = {
  document: { getElementById() { return root; } },
  window: { addEventListener(name, listener) {
    if (name === 'review:loaded') {
      const report = JSON.parse(fs.readFileSync(0, 'utf-8'));
      listener({ detail: { report, reportUrl: '/review.html' } });
    }
  } },
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf-8'), sandbox);
process.stdout.write(root.innerHTML);
"""


@unittest.skipUnless(shutil.which("node"), "Node.js is required to render guided evidence")
class GuidedPdfContextTests(unittest.TestCase):
    def evidence(self, pages=None):
        source = {
            1: "Начало длинного условия",
            2: "Досрочный выход < 60 дней считается нарушением.",
            3: "Продолжение условия с исключением.",
            4: "| Условие | Результат |",
            5: "| --- | --- |",
            6: "| Подтверждено | Открыть доступ |",
            7: "Посторонняя строка со следующей страницы.",
        }
        first = {"line": 2, "quote": "выход < 60 дней"}
        second = {"line": 6, "quote": source[6]}
        witness = {
            "scenario": "Одна заявка.",
            "first": {"source": first, "outcome": "Запретить"},
            "second": {"source": second, "outcome": "Открыть"},
        }
        report = {
            "run": {} if pages is None else {"pages": pages},
            "source": [{"line": line, "text": text} for line, text in source.items()],
            "segments": [
                {"kind": "разметка", "start": 5},
                {"kind": "строка таблицы", "start": 6, "header": 4},
            ],
            "whole": {"findings": [{
                "id": "D-01", "priority": "критический", "category": "нарушение",
                "evidence": [first, second], "question": "Как обработать заявку?",
                "candidates": [1],
            }]},
            "conflicts": [witness],
        }
        html = subprocess.run(
            ["node", "-e", HARNESS, str(GUIDED)],
            input=json.dumps(report), check=True, capture_output=True, text=True,
        ).stdout
        return ElementTree.fromstring(html).findall(".//figure")

    def test_pdf_shows_quote_and_collapsed_same_page_context(self):
        first, second = self.evidence([
            {"start": 1, "end": 6, "page": 1},
            {"start": 7, "end": 7, "page": 2},
        ])
        cite = first.find("figcaption/a")
        self.assertEqual(cite.text, "Строка 2 · с. 1 ↗")
        self.assertEqual(cite.attrib["href"], "/review.html#source-line-2")
        self.assertEqual(first.find("blockquote").text, "выход < 60 дней")

        context = first.find("details")
        self.assertNotIn("open", context.attrib)
        links = context.findall("div/a")
        self.assertEqual([link.attrib["href"] for link in links], [
            "/review.html#source-line-1", "/review.html#source-line-3",
            "/review.html#source-line-4",
        ])
        self.assertEqual(context.findall("div/blockquote")[1].text,
                         "Продолжение условия с исключением.")
        self.assertNotIn("Посторонняя", "".join(second.itertext()))
        self.assertEqual([link.attrib["href"] for link in second.findall("details/div/a")],
                         ["/review.html#source-line-4"])
        self.assertEqual([cell.text for cell in second.findall("dl/*")],
                         ["Условие", "Подтверждено", "Результат", "Открыть доступ"])

    def test_markdown_keeps_original_layout_without_pdf_metadata(self):
        first, second = self.evidence()
        self.assertEqual(first.find("figcaption/a").text, "Строка 2 ↗")
        self.assertIsNone(first.find("details"))
        self.assertIsNone(second.find("details"))
        self.assertEqual(len(second.findall("dl/dd")), 2)


if __name__ == "__main__":
    unittest.main()
