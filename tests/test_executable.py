from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from reviewer import executable


LINES = {1: "Начислить баллы за все покупки.", 2: "За день можно начислить до 60 баллов."}
REFS = [{"line": n, "quote": line} for n, line in LINES.items()]
WITNESS = {"scenario": "За покупку положено 80 баллов.",
           "first": {"outcome": "80 баллов", "source": REFS[0]},
           "second": {"outcome": "60 баллов", "source": REFS[1]}}
BASE = {"whole": {"findings": [{"id": "D-01", "priority": "критический", "category": "нарушение",
                                "triage": {"witness": [WITNESS]}}]}}


def case(points):
    return {"initial": [{"key": "total", "value": 0}],
            "events": [{"kind": "credit", "label": "Начисление",
                        "values": [{"key": "points", "value": points}]}]}


def rule(source, expr):
    return {"source": source, "rules": [{"event": "credit", "when": "True",
                                         "updates": [{"target": "total", "expr": expr}]}]}


PROGRAM = {"states": [{"key": "total", "label": "Баланс", "unit": "баллы"}],
           "inputs": [{"key": "points", "label": "Начисляемые баллы", "unit": "баллы"}],
           "observe": ["total"], "scenario": case(80), "control": case(10),
           "first": rule(REFS[0], "total + points"), "second": rule(REFS[1], "min(60, total + points)")}
PROPOSAL = {"programs": [PROGRAM], "reason": "Граница дневного начисления"}
AUDIT = {"verdict": "accepted", "first_rule": "Начислены все баллы.", "second_rule": "Применён предел.",
         "scenario": "Начисление выше лимита.", "control": "В пределах лимита.", "evidence": REFS}


class ExecutableStageTests(TestCase):
    def run_stage(self, responses):
        with patch("reviewer.full.codex_provider.complete", side_effect=responses):
            return executable.build(BASE, LINES, "test", None, "digest", 1)

    def test_replay_recomputes_numbers_and_checks_grounding(self):
        section, attempts = self.run_stage([(PROPOSAL, {}), (AUDIT, {})])
        report = {**BASE, "executables": section}
        executable.validate_saved(report, LINES)
        self.assertEqual(len(attempts), 2)
        row, = section["items"]
        self.assertEqual(row["result"]["scenario"]["first_difference"],
                         {"step": 1, "field": "total", "first": 80, "second": 60})
        self.assertIsNone(row["result"]["control"]["first_difference"])
        for mutate in (lambda r: r["result"]["scenario"]["final"]["first"].update(total=120),
                       lambda r: r["program"]["second"].update(source=REFS[0]),
                       lambda r: r["audit"].update(verdict="rejected"),
                       lambda r: r.update(finding_id="D-02")):
            damaged = deepcopy(report)
            mutate(damaged["executables"]["items"][0])
            with self.assertRaises(ValueError):
                executable.validate_saved(damaged, LINES)

    def test_semantic_rejection_causes_repair_then_explicit_abstention(self):
        section, attempts = self.run_stage([(PROPOSAL, {}), (dict(AUDIT, verdict="rejected"), {}),
                                             ({"programs": [], "reason": "Источник не задаёт лимит для этого дня."}, {})])
        self.assertEqual(len(attempts), 3)
        self.assertEqual(section["items"][0]["status"], "unsupported")
        self.assertNotIn("result", section["items"][0])
        executable.validate_saved({**BASE, "executables": section}, LINES)

    def test_failed_optional_call_preserves_the_review_and_reports_actual_attempt(self):
        section, attempts = self.run_stage([ValueError("offline")])
        self.assertEqual([(e["status"], e["usage"]) for e in attempts], [("failed", None)])
        self.assertEqual(section["items"][0]["status"], "unsupported")

    def test_fully_aligned_review_generates_no_claim_or_model_call(self):
        report = {"whole": {"findings": []}}
        with patch("reviewer.full.codex_provider.complete") as provider:
            section, attempts = executable.build(report, LINES, "test", None, "digest", 1)
        self.assertEqual(section["items"], [])
        self.assertEqual(attempts, [])
        provider.assert_not_called()

    def test_source_verification_compares_bytes_and_saved_extraction(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "акт.md"
            source.write_bytes("Строка\n\nДругая строка\n".encode())
            report = {"run": {"input_lines": 3, "input_sha256": sha256(source.read_bytes()).hexdigest()},
                      "source": [{"line": 1, "text": "Строка"}, {"line": 3, "text": "Другая строка"}]}
            self.assertEqual(executable.verify_source(report, source), report["run"]["input_sha256"])
            report["source"][1]["text"] = "Иная строка"
            with self.assertRaisesRegex(ValueError, "строки оригинального"):
                executable.verify_source(report, source)
            source.write_text("Новая версия")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                executable.verify_source(report, source)
