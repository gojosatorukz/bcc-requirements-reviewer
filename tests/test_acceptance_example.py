from copy import deepcopy
import unittest
from pathlib import Path
from unittest.mock import patch

from reviewer import acceptance_example as ae, executable
from reviewer.gemini_provider import QuotaExceeded
from reviewer.serve import Run


class FixedAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.lines = {
            1: "Одна смена кирпичного склада состоит из поставки, выдачи и сверки именно в этом порядке.",
            2: "До смены на складе ноль штук.",
            3: "Поставка добавляет 28 штук.",
            4: "Выдача уменьшает складской запас на 9 штук.",
            5: "При сверке остаток сохраняется.",
            6: "Приёмка этой смены должна показать остаток 12 штук.",
        }
        ref = lambda n: {"line": n, "quote": self.lines[n]}
        num = lambda n, literal: {"literal": literal, "source": ref(n)}
        self.program = {"metric": "Остаток на складе", "unit": "штук", "unit_source": ref(2), "scope": [ref(1)],
                        "initial": num(2, "ноль"), "expected": num(6, "12"), "steps": [
                            {"label": "Поставка", "operation": "add", "operands": [num(3, "28")], "source": ref(3)},
                            {"label": "Выдача", "operation": "subtract", "operands": [num(4, "9")], "source": ref(4)},
                            {"label": "Сверка", "operation": "keep", "operands": [], "source": ref(5)}]}
        self.witness = {"scenario": "Одна фиксированная смена", "first": {"source": ref(5), "outcome": "19 штук"},
                        "second": {"source": ref(6), "outcome": "12 штук"}}
        self.finding = {"id": "D-01", "priority": "критический", "category": "нарушение",
                        "triage": {"witness": [self.witness]}}
        self.review = {"whole": {"findings": [self.finding]}}
        self.audit = {"verdict": "accepted", "fixed_input": "Одна смена от нуля.",
                      "operations": "Поставка, выдача, сверка.", "expectation": "Остаток после смены.",
                      "evidence": [ref(i) for i in self.lines]}
        self.ordinary = {"programs": [], "reason": "Второй допустимый вход не указан."}
        self.proposal = {"examples": [self.program], "reason": "Проверяется одна смена."}

    def build(self, responses):
        with patch("reviewer.codex_provider.complete", side_effect=responses):
            return executable.build(self.review, self.lines, "test", None, "digest", 1)

    def test_full_builder_keeps_ordinary_abstention_and_replays_independent_warehouse_example(self):
        section, events = self.build([(answer, {}) for answer in (self.ordinary, self.proposal, self.audit)])
        row, = section["items"]
        self.assertEqual(row["status"], "unsupported")
        self.assertEqual(row["reason"], self.ordinary["reason"])
        self.assertEqual(len(events), 3)
        result = row["acceptance_example"]["result"]
        self.assertEqual((result["initial"], result["computed"], result["expected"]), (0, 19, 12))
        self.assertEqual([s["after"] for s in result["steps"]], [28, 19, 19])
        executable.validate_saved({**self.review, "executables": section}, self.lines)
        for key, change in (("result", {"expected": 88}), ("result", {"computed": True}),
                            ("audit", {"verdict": "rejected"}), ("audit", {"evidence": self.audit["evidence"][1:]})):
            forged = deepcopy(section)
            forged["items"][0]["acceptance_example"][key].update(change)
            with self.assertRaises(ValueError):
                executable.validate_saved({**self.review, "executables": forged}, self.lines)

    def test_arithmetic_rejects_aligned_control_expected_operands_and_unsupported_operations(self):
        source = self.lines
        def run(program):
            return ae.execute(program, source, self.witness)
        for modify in (lambda p: p["steps"][0]["operands"][0].update(literal="8"),
                       lambda p: p["steps"][1].update(operands=[p["expected"]]),
                       lambda p: p["steps"][1].update(operation="eval"),
                       lambda p: p["expected"].update(literal="19"),
                       lambda p: p["steps"][-1].update(source=p["steps"][0]["source"]),
                       lambda p: p["steps"][-1].update(operands=[p["expected"]])):
            program = deepcopy(self.program)
            modify(program)
            with self.assertRaises(ValueError):
                run(program)
        source[6] = source[6].replace("12", "19")
        self.witness["second"]["source"]["quote"] = source[6]
        self.program["expected"].update(literal="19", source=self.witness["second"]["source"])
        with self.assertRaisesRegex(ValueError, "совпадает"):
            run(self.program)

    def test_no_partial_report_on_outage_or_disagreement(self):
        for stage, responses in (("acceptance_proposal", [ValueError("private")]),
                                 ("acceptance_audit", [(self.proposal, {}), ValueError("private")])):
            section, events = self.build([(self.ordinary, {}), *responses])
            self.assertEqual(section["items"], [{"finding_id": "D-01", "status": "provider_unavailable", "stage": stage}])
            self.assertEqual(events[-1]["status"], "failed")
            executable.validate_saved({**self.review, "executables": section}, self.lines)
        with patch("reviewer.codex_provider.fallback_model", return_value="backup"):
            with self.assertRaises(executable.StageProviderError) as failure:
                self.build([(self.ordinary, {}), QuotaExceeded("HTTP 429")])
            self.assertIsInstance(failure.exception.__cause__, QuotaExceeded)
        section, _ = self.build([(answer, {}) for answer in (self.ordinary, self.proposal,
                                                           dict(self.audit, verdict="rejected"))])
        self.assertNotIn("acceptance_example", section["items"][0])

    def test_number_cannot_be_taken_from_truncated_decimal_grouped_literal_or_requirement_id(self):
        line = "К-99. В кейсе 18,70 и 1 025 единиц. Снижение на −30."
        for literal in ("99", "18", "70", "1", "025", "25", "30"):
            with self.subTest(literal=literal), self.assertRaises(ValueError):
                ae.amount({"source": {"line": 1, "quote": literal}, "literal": literal}, {1: line})
        self.assertEqual(ae.amount({"source": {"line": 1, "quote": "1 025 единиц"}, "literal": "1 025"},
                                   {1: line}), 1025)

    def test_live_progress_counts_distinct_finished_proofs_of_both_kinds(self):
        run = Run("test", "full", Path("."))
        run.record("План расчётов · примеров: 2")
        run.record("Исполняемый пример D-01 · сверен на фиксированном входе")
        self.assertEqual(run.phase, "executables")
        run.record("Исполняемый пример D-02 · подтверждён")
        self.assertEqual(run.checked_proofs, {"D-01", "D-02"})
        self.assertEqual(run.phase, "executables")
        run.record("Отчёт JSON сохранён")
        self.assertEqual(run.phase, "render")
