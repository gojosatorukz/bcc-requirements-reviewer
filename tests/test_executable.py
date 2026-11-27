from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event, Lock
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from reviewer import executable
from reviewer.execution_view import render_execution
from reviewer.full_render import execution_markdown


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


PROGRAM = {"domains": [], "states": [{"key": "total", "label": "Баланс", "unit": "баллы"}],
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

    def test_parallel_drafts_finish_in_reverse_order_but_publish_in_source_order_after_qa(self):
        findings = [dict(BASE["whole"]["findings"][0], id=f"D-{i:02d}") for i in (1, 2, 3)]
        pending = {"status": "needs_decision", "action": "Отменить начисление", "question": "В этот день?",
                   "evidence": REFS[:1]}
        checks = {"checks": [pending]}
        report = {"whole": {"findings": findings}, "lifecycle_checks": checks}
        responses = {"programs": PROPOSAL, "verdict": AUDIT}

        def run(parallel):
            finished = [Event() for _ in findings]
            after_qa = Event()
            lock = Lock()
            completed = []

            def provider(prompt, model, schema):
                body = json.loads(prompt.rsplit("\n", 1)[-1])
                field = next(iter(schema["properties"]))
                slot = int(body["final_finding"]["id"][2:]) - 1
                if field == "programs" and parallel:
                    self.assertNotIn("unresolved_actions", body)
                    if slot + 1 < len(finished):
                        self.assertTrue(finished[slot + 1].wait(5), "Следующий черновик не стартовал")
                    with lock:
                        completed.append(slot)
                    finished[slot].set()
                else:
                    self.assertEqual(body["unresolved_actions"][0]["question"], pending["question"])
                    if parallel:
                        self.assertTrue(after_qa.is_set(), "Аудит стартовал до QA")
                return deepcopy(responses[field]), {}

            def qa():
                self.assertTrue(finished[0].wait(5))
                after_qa.set()
                return checks

            with patch("reviewer.full.codex_provider.complete", side_effect=provider):
                if parallel:
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        job = pool.submit(qa)
                        result, _ = executable.build(report, LINES, "test", None, "d", 3, qa_checks=job.result)
                    self.assertEqual(completed, [2, 1, 0])
                else:
                    result, _ = executable.build(report, LINES, "test", None, "d", 1)
            return result

        self.assertEqual(run(False), run(True))

    def test_material_contract_conflict_needs_two_cited_sides_and_adjudication(self):
        finding = {"id": "D-02", "priority": "существенный", "category": "нарушение",
                   "candidates": [1], "triage": {"witness": []}, "evidence": REFS}
        report = {"whole": {"findings": [finding]}, "conflicts": [WITNESS]}
        self.assertEqual(executable.targets(report), {"D-02": WITNESS})
        self.assertEqual(executable.targets(report, "witness-v1"), {})
        for edit in ({"category": "открытый вопрос"}, {"evidence": REFS[:1]}, {"candidates": [1, 2]}):
            check = dict(report, whole={"findings": [dict(finding, **edit)]})
            self.assertEqual(executable.targets(check), {})
        old = dict(report, executables={"version": "witness-v1", "items": []})
        executable.validate_saved(old, LINES)

    def test_missing_contract_does_not_become_a_second_observed_result(self):
        finding = {"id": "D-03", "priority": "существенный", "category": "нарушение",
                   "candidates": [1], "evidence": REFS, "reason": "Вторая операция не определена."}
        report = {"whole": {"findings": [finding]}, "conflicts": [WITNESS]}
        scope = {"relation": "not_established", "first_requires": "Названо назначение первой операции.",
                 "second_requires": "У второй описан результат.", "reason": "Уточните контракт первого вызова.",
                 "evidence": REFS}
        with patch("reviewer.full.codex_provider.complete", return_value=(scope, {})) as provider:
            section, attempts = executable.build(report, LINES, "test", None, "digest", 1)
            self.assertEqual(provider.call_count, 1)
            self.assertIn(finding["reason"], provider.call_args.args[0])
        row, = section["items"]
        self.assertEqual((row["status"], row["reason"]), ("unsupported", scope["reason"]))
        self.assertEqual(len(attempts), 1)
        executable.validate_saved({**report, "executables": section}, LINES)
        accepted = dict(scope, relation="same_operation")
        with patch("reviewer.full.codex_provider.complete", side_effect=[(accepted, {}), (PROPOSAL, {}), (AUDIT, {})]):
            section, attempts = executable.build(report, LINES, "test", None, "digest", 1)
        self.assertEqual(section["items"][0]["status"], "executed")
        executable.validate_saved({**report, "executables": section}, LINES)
        section["items"][0]["scope_assessment"] = scope
        with self.assertRaisesRegex(ValueError, "основания"):
            executable.validate_saved({**report, "executables": section}, LINES)
        del section["items"][0]["scope_assessment"]
        with self.assertRaisesRegex(ValueError, "основания"):
            executable.validate_saved({**report, "executables": section}, LINES)

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

    def test_provider_outages_distinguish_proposal_and_audit_without_saving_diagnostics(self):
        diagnostic = "private provider diagnostic"
        for stage, earlier in (("proposal", []), ("audit", [(PROPOSAL, {})])):
            with self.subTest(stage=stage):
                section, attempts = self.run_stage([*earlier, ValueError(diagnostic)])
                row, = section["items"]
                self.assertEqual(row, {"finding_id": "D-01", "status": "provider_unavailable", "stage": stage})
                self.assertEqual((attempts[-1]["status"], attempts[-1]["usage"]), ("failed", None))
                self.assertNotIn(diagnostic, str(attempts))
                executable.validate_saved({**BASE, "executables": section}, LINES)
                for rendered in (render_execution(row), execution_markdown(row)):
                    self.assertIn("Модель недоступна", rendered)
                    self.assertIn(executable.PROVIDER_FAILURES[stage], rendered)
                    self.assertNotIn(diagnostic, rendered)
                for edit in ({"stage": "unknown"}, {"reason": diagnostic}, {"result": {}}):
                    forged = {**section, "items": [{**row, **edit}]}
                    with self.assertRaises(ValueError):
                        executable.validate_saved({**BASE, "executables": forged}, LINES)

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
