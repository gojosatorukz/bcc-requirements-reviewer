from base64 import b64decode
import csv
from io import StringIO
import json
from pathlib import Path
import re
import unittest

from reviewer.full_render import render_full
from reviewer.jira_export import csv_cell, issues_csv


class JiraExportTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "runs/bcc-release/review.json"
        self.report = json.loads(path.read_bytes())

    def rows(self):
        return list(csv.DictReader(StringIO(issues_csv(self.report).decode("utf-8-sig"), newline="")))

    def test_official_report_exports_open_findings_and_both_replayed_outcomes(self):
        rows = self.rows()
        findings = self.report["whole"]["findings"]
        self.assertEqual(len(rows), len(findings))
        for row, finding in zip(rows, findings):
            self.assertEqual(set(row), {"Summary", "Issue Type", "Priority", "Description"})
            self.assertTrue(row["Summary"].endswith(f"[{finding['id']}]"))
            self.assertEqual(row["Issue Type"], "Task")
            self.assertIn(finding["question"], row["Description"])
            self.assertIn(finding["owner"], row["Description"])
            self.assertIn(finding["evidence"][0]["quote"], row["Description"])
        self.assertEqual(rows[0]["Priority"], "Highest")
        self.assertIn("А = 0 ₸, Б = 4000 ₸", rows[0]["Description"])
        self.assertIn("контрольном примере обе трактовки дают 4000 ₸", rows[0]["Description"])
        self.assertIn("А = 1", rows[1]["Description"])
        self.assertIn("Б = 2", rows[1]["Description"])
        page = render_full(self.report)
        payload = re.search(r'download="review-issues.csv" href="data:text/csv;charset=utf-8;base64,([^"]+)', page)[1]
        self.assertEqual(b64decode(payload, validate=True), issues_csv(self.report))

    def test_russian_csv_round_trip_spreadsheet_guards_and_summary_length(self):
        finding = self.report["whole"]["findings"][0]
        finding["title"] = '\ufeff　=1+1 "апрель, май"' + "ы" * 300
        finding["question"] = 'Что делать с меткой "Май, июнь"?\nВторая строка.'
        row = self.rows()[0]
        self.assertTrue(row["Summary"].startswith("'"))
        self.assertIn('"апрель, май"', row["Summary"])
        self.assertLessEqual(len(row["Summary"]), 255)
        self.assertIn(finding["question"], row["Description"])
        for hostile in (" +cmd", "\t@value", "\rpayload", "＝foo", "-cmd"):
            self.assertTrue(csv_cell(hostile).startswith("'"))

    def test_no_tasks_when_all_hypotheses_are_reconciled(self):
        self.report["whole"]["findings"] = []
        self.assertEqual(self.rows(), [])
