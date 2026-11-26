from copy import deepcopy
import unittest

from reviewer import priority


class PriorityTest(unittest.TestCase):
    def setUp(self):
        self.lines = {4: "Состояние A.", 6: "Состояние B."}
        a, b = [{"line": n, "quote": s} for n, s in self.lines.items()]
        self.case = {"scenario": "Тот же клиент и тот же платёж.",
                     "first": {"source": a, "outcome": "A"}, "second": {"source": b, "outcome": "B"}}
        self.finding = {"evidence": [a, b], "category": "нарушение",
                        "triage": {"rule": priority.CONFLICT, "witness": [self.case]}}

    def test_rank_ignores_wording_and_order(self):
        a = deepcopy(self.finding)
        a.update(title="Другая формулировка", requirements=["R-024"], candidates=[8])
        a["evidence"].reverse()
        self.assertEqual(priority.derive_priority(a, self.lines), "критический")
        for rule, rank in [(priority.DELIVERY, "существенный"), (priority.EDITORIAL, "редакционный")]:
            a["triage"] = {"rule": rule, "witness": []}
            for category in ("нарушение", "открытый вопрос"):
                a["category"] = category
                self.assertEqual(priority.derive_priority(a, self.lines), rank)

    def test_conflict_requires_two_distinct_outcomes_and_grounded_quotes(self):
        for replacement in ({"source": self.case["first"]["source"], "outcome": "B"},
                            {"source": self.case["second"]["source"], "outcome": "A"},
                            {"source": {"line": 6, "quote": "Выдуманная цитата"}, "outcome": "B"}):
            with self.subTest(replacement=replacement):
                candidate = deepcopy(self.finding)
                candidate["triage"]["witness"][0]["second"] = replacement
                with self.assertRaises(ValueError):
                    priority.derive_priority(candidate, self.lines)
        self.finding["category"] = "открытый вопрос"
        with self.assertRaises(ValueError):
            priority.derive_priority(self.finding, self.lines)

    def test_editorial_rule_cannot_carry_a_business_conflict_scenario(self):
        self.finding["triage"]["rule"] = priority.EDITORIAL
        with self.assertRaises(ValueError):
            priority.derive_priority(self.finding, self.lines)
