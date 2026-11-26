import unittest

from reviewer.challenge import check_decisions


class ChallengeTest(unittest.TestCase):
    def test_rejects_invented_quote(self):
        dec = {"id": 1, "verdict": "конфликт", "reason": "Есть расхождение",
               "evidence": [{"line": 7, "quote": "Фраза"}]}
        with self.assertRaises(ValueError):
            check_decisions({"decisions": [dec]}, 1, {7: "Другой текст"})

    def test_requires_one_decision_per_candidate(self):
        dec = {"id": 1, "verdict": "совместимо", "reason": "Указано исключение",
               "evidence": [{"line": 7, "quote": "Фраза"}]}
        check_decisions({"decisions": [dec]}, 1, {7: "Фраза"})
        with self.assertRaises(ValueError):
            check_decisions({"decisions": [dec, dec]}, 2, {7: "Фраза"})
