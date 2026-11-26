import json
from pathlib import Path
import re
import unittest

from reviewer.full_render import render_decision_drafts
from reviewer.render import render_report


class DecisionDraftTest(unittest.TestCase):
    def setUp(self):
        self.candidate = {
            "title": "Куда направить заявку",
            "scenario": "Получена заявка <A>.",
            "first": {"outcome": "Направить в очередь A.", "source": {"line": 2}},
            "second": {"outcome": "Направить в очередь B.", "source": {"line": 5}},
            "agreement_control": "Срочную заявку принимают обе очереди.",
        }
        self.finding = {
            "id": "D-03",
            "priority": "критический",
            "category": "нарушение",
            "candidates": [1],
            "evidence": [{"line": 2}, {"line": 5}],
        }

    def test_two_unselected_choices_with_source_links_and_drafts(self):
        html = render_decision_drafts(self.finding, [self.candidate])
        radios = re.findall(r'<input[^>]*>', html)
        self.assertEqual(len(radios), 2)
        for number, line, queue in [(1, 2, "A"), (2, 5, "B")]:
            self.assertIn('name="draft-D-03-1"', radios[number - 1])
            self.assertIn(f'value="{number}"', radios[number - 1])
            self.assertNotIn("checked", radios[number - 1])
            draft = html.split(f'class="draft-result draft-result-{number}">')[1].split("</div>")[0]
            self.assertIn("<b>Сценарий.</b> Получена заявка &lt;A&gt;.", draft)
            self.assertIn(f"<b>Ожидаемый результат.</b> Направить в очередь {queue}.", draft)
            self.assertIn('href="#source-line-' + str(line) + '"', draft)
            self.assertIn("Срочную заявку принимают обе очереди.", draft)
        self.assertNotIn("<A>", html)

    def test_distinct_radio_groups_for_multiple_candidates(self):
        self.finding["candidates"] = [1, 2]
        html = render_decision_drafts(self.finding, [self.candidate, self.candidate])
        groups = re.findall(r'<input[^>]*name="([^"]+)"', html)
        self.assertEqual(groups, ["draft-D-03-1"] * 2 + ["draft-D-03-2"] * 2)

    def test_ineligible_findings_have_no_controls(self):
        for override in (
            {"priority": "существенный"},
            {"category": "открытый вопрос"},
            {"candidates": []},
            {"evidence": [{"line": 2}]},
        ):
            with self.subTest(override=override):
                self.assertEqual(render_decision_drafts(self.finding | override, [self.candidate]), "")

    def test_full_report_places_controls_inside_eligible_findings(self):
        path = Path(__file__).resolve().parents[1] / "runs" / "bcc-final" / "review.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        html = render_report(report)
        articles = re.findall(r'<article id="(D-\d+)" class="finding">(.*?)</article>', html, re.S)
        actual = {finding_id for finding_id, content in articles if '<details class="decision-draft">' in content}
        expected = {
            finding["id"] for finding in report["whole"]["findings"]
            if finding["priority"] == "критический" and finding["candidates"]
        }
        self.assertTrue(expected)
        self.assertEqual(actual, expected)
        self.assertNotIn("<script", html)
