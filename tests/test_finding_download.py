from base64 import b64decode
from html.parser import HTMLParser
import json
from pathlib import Path
import unittest

from reviewer.full_render import finding_download, finding_markdown
from reviewer.render import render_report


class Downloads(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("class") == "finding-export":
            self.links.append(attrs)

    def markdown(self, index):
        prefix, data = self.links[index]["href"].split(",", 1)
        if prefix != "data:text/markdown;charset=utf-8;base64":
            raise ValueError(prefix)
        return b64decode(data, validate=True).decode("utf-8")


class FindingDownloadTest(unittest.TestCase):
    def setUp(self):
        self.finding = {
            "id": "D-02",
            "title": 'Спор о "месяце" <img src=x onerror=alert(1)>',
            "category": "открытый вопрос",
            "priority": "существенный",
            "owner": 'Аналитик "API"',
            "reason": "1. У ТЗ два исхода.",
            "question": "[Какой исход?](javascript:alert(1))\n# Новый заголовок",
            "action": "Уточнить правило.",
            "evidence": [{"line": 7, "quote": '```` <script>alert("x")</script>'}],
        }
        self.run = {"input_name": 'ТЗ "API".pdf', "input_sha256": "a" * 64}

    def test_download_roundtrips_and_protects_markdown_content(self):
        html = finding_download(self.finding, self.run, {7: 4})
        downloads = Downloads(html)
        md = downloads.markdown(0)

        self.assertEqual(downloads.links[0]["download"], "D-02.md")
        self.assertNotIn("onerror", html)
        self.assertIn('Спор о "месяце" \\<img src=x onerror=alert(1)\\>', md)
        self.assertIn('ТЗ "API".pdf', md)
        self.assertIn("a" * 64, md)
        self.assertIn('Аналитик "API"', md)
        self.assertIn("открытый вопрос", md)
        self.assertIn("черновик для согласования", md)
        self.assertIn("1\\. У ТЗ два исхода.", md)
        self.assertIn("\\[Какой исход?\\](javascript\\:alert(1)) \\# Новый заголовок", md)
        self.assertIn('Строка 7, страница 4\n\n`````text\n```` <script>alert("x")</script>\n`````', md)

    def test_empty_evidence_and_no_pdf_page(self):
        self.assertIn("Строка 7\n", finding_markdown(self.finding, self.run, {}))
        self.assertNotIn("страница", finding_markdown(self.finding, self.run, {}))
        self.finding["evidence"] = []
        self.assertIn("Цитаты в отчёте отсутствуют.", finding_markdown(self.finding, self.run, {}))

    def test_cold_official_download_contains_both_replayed_cases_with_source_context(self):
        path = Path(__file__).resolve().parents[1] / "eval/executable/runs/official.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        downloads = Downloads(render_report(report))
        md = downloads.markdown(0)
        self.assertIn("## Исполняемый пример", md)
        self.assertIn("Правило А даёт 0 ₸, правило Б даёт 4\u202f000 ₸", md)
        self.assertIn("Правило А", md)
        self.assertIn("строка 38", md)
        self.assertIn("строка 64", md)
        self.assertIn("### Контрольный пример", md)
        self.assertIn("совпали на каждом шаге", md)
        self.assertIn("предстоит согласовать", md)
        self.assertIn("## Цитаты из исходника", md)

    def test_an_unsupported_computation_keeps_its_reason_without_a_numeric_claim(self):
        execution = {"finding_id": "D-02", "status": "unsupported", "reason": "Не хватает <срока>."}
        md = finding_markdown(self.finding, self.run, {}, execution)
        self.assertIn("Расчёт не подтверждён", md)
        self.assertIn(r"Не хватает \<срока\>", md)
        self.assertNotIn("Контрольный пример", md)

    def test_full_render_offers_all_findings_without_javascript(self):
        path = Path(__file__).resolve().parents[1] / "runs" / "bcc-priority" / "review.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        html = render_report(report)
        downloads = Downloads(html)
        findings = report["whole"]["findings"]
        self.assertEqual([link["download"] for link in downloads.links], [f'{f["id"]}.md' for f in findings])
        self.assertNotIn("<script", html)
        for index, finding in enumerate(findings):
            self.assertTrue(downloads.markdown(index).startswith("# " + finding["id"]))
