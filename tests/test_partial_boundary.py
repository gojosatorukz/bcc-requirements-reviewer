from unittest import TestCase

from reviewer.partial import cells
from reviewer.partial_boundary import boundary_questions
from reviewer.segments import segment


def questions(rule, rows, heading="Acceptance examples"):
    lines = ["# Curing cabinet", *rule.splitlines(), "## " + heading,
             "| Profile | Temperature |", "| --- | --- |", *rows]
    return list(boundary_questions(lines, segment(lines), cells))


class PartialBoundaryTests(TestCase):
    rule = "Below 42 degrees select Amber. At least 42 degrees selects Violet."
    rows = ["| Amber | 41 |", "| Amber | 42 |", "| Violet | 43 |"]

    def test_compares_equality_with_two_controls_and_supplies_original_lines(self):
        (finding,) = questions(self.rule, self.rows)
        self.assertEqual(finding[0], "table_cutoff")
        self.assertIn("42", finding[2])
        self.assertIn("«Violet» по формулировке", finding[2])
        self.assertEqual(finding[3], [2, 4, 7])
        self.assertEqual(questions(self.rule, [self.rows[0], "| Violet | 42 |", self.rows[2]]), [])

    def test_independent_rows_and_both_russian_comparisons(self):
        rule = "Не более 42 градусов — Amber.\nВыше 42 градусов — Violet."
        rows = [self.rows[2], "| Violet | 42 |", self.rows[0]]
        (finding,) = questions(rule, rows, heading="Приёмка")
        self.assertIn("«Amber» по формулировке", finding[2])
        self.assertEqual(finding[3], [2, 3, 5, 8])

    def test_skips_ambiguous_contexts_and_incomplete_controls(self):
        for rule, rows, heading in (
            (self.rule, self.rows[1:], "Acceptance"),
            (self.rule, self.rows + ["| Violet | 42 |"], "Examples"),
            (self.rule, self.rows, "Unrelated equipment"),
            (self.rule, ["| Violet | 41 |", *self.rows[1:]], "Acceptance"),
            ("Below 42 unless maintenance is active use Amber. At least 42 selects Violet.", self.rows, "Examples"),
            ("Below 42 selects Amber. Below 43 selects Violet.", self.rows, "Examples"),
            ("Below 42 selects Amber. At least 42 does not select Violet.", self.rows, "Examples"),
            ("Below 42.5 selects Amber. At least 42.5 selects Violet.", self.rows, "Examples"),
        ):
            with self.subTest(rule=rule, rows=rows, heading=heading):
                self.assertEqual(questions(rule, rows, heading), [])
