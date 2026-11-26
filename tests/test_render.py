import copy
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from reviewer.render import render_report


ROOT = Path(__file__).resolve().parents[1]
CASE = {
    "title": "Два порядка обработки",
    "scenario": "Один платёж и два договора",
    "first": {"source": {"line": 4, "quote": "по сроку платежа"}, "outcome": "Платёж получает договор А"},
    "second": {"source": {"line": 9, "quote": "по дате договора"}, "outcome": "Платёж получает договор Б"},
    "owner": "Владелец продукта",
    "question": "Сортировать по сроку платежа или по дате договора?",
    "agreement_control": "При одинаковом порядке дат оба правила выбирают договор А.",
}
REPORT = {
    "schema_version": 1,
    "scope": "Проверка поведенческих противоречий",
    "run": {
        "started_utc": "2026-11-26T17:53:54+00:00",
        "elapsed_seconds": 14.28,
        "provider": "codex-cli",
        "model": "test-model",
        "agent_version": "test-version",
        "input_name": "spec.md",
        "input_sha256": "a" * 64,
        "input_lines": 9,
        "usage": {"input_tokens": 123, "output_tokens": 45},
    },
    "source": [
        {"line": 1, "text": "# Тестовое ТЗ"},
        {"line": 4, "text": "Сортировать по сроку платежа."},
        {"line": 9, "text": "Сортировать по дате договора."},
    ],
    "conflicts": [CASE],
}


class ReportMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class RenderTests(unittest.TestCase):
    def test_all_citations_have_the_right_source_and_return_link(self):
        report = copy.deepcopy(REPORT)
        other = copy.deepcopy(CASE)
        other["first"]["source"] = {"line": 1, "quote": "Тестовое ТЗ"}
        report["conflicts"].append(other)
        html = render_report(report)
        markup = ReportMarkup(html)
        ids = [attrs["id"] for _, attrs in markup.tags if "id" in attrs]
        self.assertEqual(len(ids), len(set(ids)))
        citations = (
            ("conflict-1-outcome-1", 4),
            ("conflict-1-outcome-2", 9),
            ("conflict-2-outcome-1", 1),
            ("conflict-2-outcome-2", 9),
        )
        for outcome, line in citations:
            with self.subTest(outcome=outcome):
                outcome_section = html.split(f'id="{outcome}"', 1)[1].split("</div>", 1)[0]
                source_row = html.split(f'id="source-line-{line}"', 1)[1].split("</li>", 1)[0]
                self.assertIn(f'href="#source-line-{line}"', outcome_section)
                self.assertIn(f'href="#{outcome}"', source_row)
                self.assertIn('tabindex="-1"', source_row)
                self.assertIn('tabindex="-1"', outcome_section)
                text = next(row["text"] for row in report["source"] if row["line"] == line)
                self.assertIn(text, source_row)
        for field in ("title", "scenario", "question", "owner", "agreement_control"):
            self.assertIn(CASE[field], html)
        for side in ("first", "second"):
            self.assertIn(CASE[side]["outcome"], html)
            self.assertIn(CASE[side]["source"]["quote"], html)
        for field in ("model", "provider", "input_sha256", "agent_version", "started_utc", "elapsed_seconds"):
            self.assertIn(str(REPORT["run"][field]), html)
        self.assertIn("<dd>123</dd>", html)

    def test_hostile_input_and_model_output_remain_plain_text(self):
        payload = '</title></style><script>alert(1)</script><img src="https://invalid.example" onerror=alert(2)> & \''
        injected = []

        def hostile(label):
            value = f"{payload} {label}"
            injected.append(value)
            return value

        report = copy.deepcopy(REPORT)
        report["scope"] = hostile("scope")
        report["run"]["input_name"] = hostile("input_name")
        report["run"]["model"] = hostile("model")
        report["run"]["usage"] = {hostile("usage label"): hostile("usage value")}
        report["run"]["engine_sha256"] = hostile("engine_sha256")
        report["run"]["attempts"] = [
            {"status": "rejected", "reason": hostile("rejection reason"), "usage": None},
            {"status": "accepted", "usage": {"output_tokens": 42}},
        ]
        conflict = report["conflicts"][0]
        for field in ("title", "scenario", "owner", "question", "agreement_control"):
            conflict[field] = hostile(field)
        for side, row in zip(("first", "second"), report["source"][1:]):
            quote = hostile(f"{side} quote")
            row["text"] = f"{hostile(f'{side} line')} {quote}"
            conflict[side]["source"]["quote"] = quote
            conflict[side]["outcome"] += hostile(f"{side} outcome")

        html = render_report(report)
        self.assertNotIn(payload, html)
        for value in injected:
            self.assertIn(escape(value), html)
        self.assertIn("<summary>Попытки · 2</summary>", html)
        self.assertIn("<strong>Отклонена</strong>", html)
        self.assertIn("<strong>Принята</strong>", html)
        self.assertIn("<dd>42</dd>", html)
        markup = ReportMarkup(html)
        for tag, attrs in markup.tags:
            self.assertNotIn(tag, {"script", "img", "iframe", "link", "base"})
            self.assertNotIn("src", attrs)
            self.assertFalse(any(name.startswith("on") for name in attrs))
            if "href" in attrs:
                self.assertTrue(attrs["href"].startswith("#"), attrs)

    def test_zero_conflicts_keeps_source_and_passport(self):
        report = copy.deepcopy(REPORT)
        report["conflicts"] = []
        report["run"]["usage"] = None
        html = render_report(report)
        self.assertIn("Поведенческие противоречия не найдены", html)
        self.assertIn("Модель вернула 0 конфликтов", html)
        self.assertIn("Этот вывод относится к данному прогону", html)
        self.assertIn('id="source-line-4"', html)
        self.assertIn('id="passport"', html)
        self.assertIn("Нет данных", html)
        self.assertNotIn('class="conflict"', html)

    def test_citation_mismatch_is_reported(self):
        report = copy.deepcopy(REPORT)
        report["conflicts"][0]["second"]["source"]["line"] = 4
        with self.assertRaisesRegex(ValueError, "исход 2: цитата не найдена в строке 4"):
            render_report(report)

    def test_cli_embeds_css_and_refuses_to_replace_input(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "result.json"
            output = Path(folder) / "offline" / "report.html"
            data = json.dumps(REPORT, ensure_ascii=False)
            source.write_text(data, encoding="utf-8")
            command = [sys.executable, "-m", "reviewer.render", str(source)]
            result = subprocess.run([*command, str(output)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            html = output.read_text(encoding="utf-8")
            self.assertIn('<html lang="ru">', html)
            self.assertIn("<style>", html)
            self.assertIn("@media (max-width: 45rem)", html)

            result = subprocess.run([*command, str(source)], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Пути JSON и HTML должны различаться", result.stderr)
            self.assertEqual(source.read_text(encoding="utf-8"), data)


if __name__ == "__main__":
    unittest.main()
