import copy
import hashlib
import json
import unittest
from pathlib import Path

from eval.make_bcc_aligned import ACCEPTANCE_LINE, ALIGNED, OFFICIAL_SHA256, ORIGINAL, make_aligned
from eval.score_review import FIXTURES, score_report

ROOT = Path(__file__).resolve().parents[1]


def read_report(relative_path):
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


class ScoreReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.official = read_report("runs/bcc-final/review.json")

    def test_saved_reports_match_controls(self):
        for fixture, path, claims in (
            ("bcc", "runs/bcc-final/review.json", [1, 1]),
            ("payment", "eval/holdout/runs/payment-approval.json", [1]),
            ("payment-aligned", "eval/holdout/runs/payment-approval-aligned.json", [0]),
        ):
            with self.subTest(fixture=fixture):
                result = score_report(read_report(path), fixture)
                self.assertTrue(result["structural_check_passed"])
                self.assertEqual([check["matched_claims"] for check in result["checks"]], claims)

    def test_true_control_requires_critical_violation_in_full_report(self):
        for changes in (
            {"priority": "существенный"},
            {"category": "открытый вопрос"},
        ):
            with self.subTest(changes=changes):
                report = copy.deepcopy(self.official)
                report["whole"]["findings"][0].update(changes)
                self.assertFalse(score_report(report, "bcc")["structural_check_passed"])

    def test_quote_cannot_match_by_line_alone(self):
        report = copy.deepcopy(self.official)
        unrelated = "В расходы входят проведённые покупки."
        report["conflicts"][0]["first"]["source"]["quote"] = unrelated
        report["whole"]["findings"][0]["evidence"][0]["quote"] = unrelated
        result = score_report(report, "bcc")
        self.assertFalse(result["structural_check_passed"])
        self.assertEqual(result["checks"][0]["matched_claims"], 0)
        self.assertTrue(result["manual_review_required"])
        self.assertEqual(result["checks"][0]["same_line_candidates_for_review"][0]["index"], 1)
        self.assertEqual(result["unassigned_critical_findings_for_review"][0]["id"], "D-01")

    def test_aligned_calendar_case_flags_surviving_violation_at_any_priority(self):
        report = copy.deepcopy(self.official)
        report["run"]["input_sha256"] = FIXTURES["bcc-aligned"][0]
        for row in report["source"]:
            if row["line"] == ACCEPTANCE_LINE:
                row["text"] = row["text"].replace("Февральский", "Январский")
        report["conflicts"][0]["second"]["source"]["quote"] = ALIGNED.decode()
        self.assertFalse(score_report(report, "bcc-aligned")["structural_check_passed"])
        report["whole"]["findings"][0]["priority"] = "существенный"
        self.assertFalse(score_report(report, "bcc-aligned")["structural_check_passed"])

        # A preliminary candidate can remain in the report after final-stage rejection.
        report["whole"]["findings"].pop(0)
        result = score_report(report, "bcc-aligned")
        self.assertTrue(result["structural_check_passed"])
        self.assertEqual([check["matched_claims"] for check in result["checks"]], [0, 1])
        self.assertEqual(len(result["checks"][0]["candidates"]), 1)

    def test_aligned_payment_rejects_a_critical_claim_but_allows_a_question(self):
        report = read_report("eval/holdout/runs/payment-approval-aligned.json")
        source = {row["line"]: row["text"] for row in report["source"]}
        finding = {
            "id": "D-01", "title": "Изменение суммы", "category": "нарушение",
            "priority": "критический", "candidates": [],
            "evidence": [{"line": line, "quote": source[line]} for line in (9, 13)],
        }
        report["whole"] = {"findings": [finding]}
        self.assertFalse(score_report(report, "payment-aligned")["structural_check_passed"])
        finding.update(category="открытый вопрос", priority="существенный")
        self.assertTrue(score_report(report, "payment-aligned")["structural_check_passed"])
        finding.update(category="нарушение", priority="критический", evidence=[])
        result = score_report(report, "payment-aligned")
        self.assertFalse(result["structural_check_passed"])
        self.assertTrue(result["manual_review_required"])
        self.assertEqual(result["unassigned_critical_findings_for_review"], [finding])

    def test_aligned_generator_changes_exactly_one_line(self):
        source = {row["line"]: row["text"] for row in self.official["source"]}
        data = ("\n".join(source.get(line, "") for line in range(1, 199)) + "\n").encode()
        self.assertEqual(hashlib.sha256(data).hexdigest(), OFFICIAL_SHA256)
        aligned = make_aligned(data)
        self.assertEqual(hashlib.sha256(aligned).hexdigest(), FIXTURES["bcc-aligned"][0])
        first = data.splitlines(keepends=True)
        second = aligned.splitlines(keepends=True)
        self.assertEqual(len(first), len(second))
        changes = [(line, old, new) for line, (old, new) in enumerate(zip(first, second), 1) if old != new]
        self.assertEqual(changes, [(ACCEPTANCE_LINE, ORIGINAL + b"\n", ALIGNED + b"\n")])
        with self.assertRaises(ValueError):
            make_aligned(aligned)


if __name__ == "__main__":
    unittest.main()
