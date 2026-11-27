from copy import deepcopy
import unittest

from reviewer import lifecycle


class LifecycleContractTest(unittest.TestCase):
    lines = {3: "Пустая корзина сохраняется только как черновик."}

    def setUp(self):
        self.check = {
            "axis": "initial_action", "status": "specified", "situation": "Новая корзина пуста.",
            "action": "Сохранить.", "observation": "Получен черновик.", "question": None,
            "evidence": [{"line": 3, "quote": "только как черновик"}],
        }
        self.result = {"checks": [self.check], "skipped": [
            {"axis": axis, "reason": "ТЗ описывает только сохранение новой корзины."}
            for axis in reversed(lifecycle.AXES[1:])
        ]}

    def test_saved_and_live_contract_agree(self):
        lifecycle.validate(self.result, self.lines)
        saved = lifecycle.materialize(self.result)
        lifecycle.validate_saved(saved, self.lines)
        self.assertEqual(saved["checks"][0]["question"], "")
        self.assertEqual([row["axis"] for row in saved["skipped"]], list(lifecycle.AXES[1:]))
        self.check.update(status="needs_decision", question="Можно ли сохранить пустую корзину?")
        saved = lifecycle.materialize(self.result)
        lifecycle.validate_saved(saved, self.lines)
        self.assertEqual(saved["checks"][0]["question"], self.check["question"])

    def test_rejects_missing_or_repeated_axis_open_question_and_bad_evidence(self):
        changes = [
            lambda r: r["skipped"].pop(),
            lambda r: r["skipped"][0].update(axis="initial_action"),
            lambda r: r["checks"][0].update(question="Что сохранить?"),
            lambda r: r["checks"][0].update(status="needs_decision"),
            lambda r: r["checks"][0].update(status="needs_decision", question=42),
            lambda r: r["checks"][0].update(evidence=[]),
            lambda r: r["checks"][0]["evidence"][0].update(quote="Оформить заказ"),
        ]
        for i, change in enumerate(changes):
            with self.subTest(i=i):
                broken = deepcopy(self.result)
                change(broken)
                with self.assertRaises(ValueError):
                    lifecycle.validate(broken, self.lines)

    def test_saved_record_requires_known_version_and_preserves_question_state(self):
        for version in (True, 2):
            with self.assertRaises(ValueError):
                lifecycle.validate_saved(dict(lifecycle.materialize(self.result), version=version), self.lines)
        saved = lifecycle.materialize(self.result)
        saved["checks"][0]["status"] = "needs_decision"
        with self.assertRaises(ValueError):
            lifecycle.validate_saved(saved, self.lines)
