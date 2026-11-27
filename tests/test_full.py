from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reviewer.full import AXES, analyze, check_batch, check_critical_sources, local_batches, original_id, run_required
from threading import Barrier, Event
from reviewer import lifecycle
from reviewer.render import render_report


class FullReviewTest(unittest.TestCase):
    def test_required_jobs_stop_dispatching_after_a_failure_and_keep_successes_in_input_order(self):
        started = []

        def quota():
            started.append("first")
            raise ValueError("quota_exhausted")

        with self.assertRaisesRegex(ValueError, "quota_exhausted"):
            run_required([quota, lambda: started.append("next")], 1)
        self.assertEqual(started, ["first"])
        wave = Barrier(4, timeout=2)

        def unavailable():
            wave.wait()
            raise ValueError("quota_exhausted")

        with self.assertRaisesRegex(ValueError, "quota_exhausted"):
            run_required([unavailable] * 4 + [lambda: started.append("extra")] * 8, 4)
        self.assertEqual(started, ["first"])
        release = Event()

        def slow_first():
            self.assertTrue(release.wait(2))
            return "first"

        def fast_second():
            release.set()
            return "second"

        self.assertEqual(run_required([slow_first, fast_second], 2), ["first", "second"])
        self.assertEqual(run_required([], 2), [])

    def test_balancing_preserves_order_and_handles_oversize_sections(self):
        blocks = [{"start": i, "end": i} for i in range(1, 124)]
        source = {i: "x" * (4000 if i == 42 else 35) for i in range(1, 124)}
        groups = list(local_batches(blocks, source))
        self.assertEqual([b for group in groups for b in group], blocks)
        self.assertTrue(all(0 < len(group) <= 26 for group in groups))
        self.assertEqual(list(local_batches([], {})), [])

    def test_short_api_examples_cannot_monopolize_a_local_worker(self):
        blocks = [{"start": i, "end": i, "kind": "пример json" if i % 2 else "заголовок"}
                  for i in range(1, 16)]
        groups = list(local_batches(blocks, {i: "{}" for i in range(1, 16)}))
        self.assertEqual([b for group in groups for b in group], blocks)
        self.assertTrue(all(sum(b["kind"] == "пример json" for b in group) <= 3 for group in groups))

    def test_critical_candidate_requires_both_source_lines(self):
        candidate = {"first": {"source": {"line": 2}}, "second": {"source": {"line": 4}}}
        f = {"priority": "критический", "candidates": [1], "evidence": [{"line": 2}]}
        with self.assertRaisesRegex(ValueError, "2, 4"):
            check_critical_sources({"findings": [f]}, [candidate])
        f["evidence"].append({"line": 4})
        check_critical_sources({"findings": [f]}, [candidate])

    def test_missing_block_and_empty_api_example_are_rejected(self):
        b = [{"start": 1, "end": 3, "kind": "пример json"}]
        with self.assertRaises(ValueError):
            check_batch({"rows": []}, b, {2: "{}"})
        with self.assertRaises(ValueError):
            check_batch({"rows": [{"start": 1, "items": []}]}, b, {2: "{}"})

    def test_full_pipeline_no_silent_omissions_or_duplicate_count(self):
        lines = ["# Тест", "X-1. Вернуть OK."]
        source = [{"line": i, "text": t} for i, t in enumerate(lines, 1)]
        ref = {"line": 2, "quote": lines[1]}
        unit = {"role": "требование", "statement": "Вернуть OK", "evidence": 2,
                "status": "нужно уточнение", "assessment": "Какое условие запускает ответ?"}
        local = {"rows": [{"start": 1, "note": "Название", "items": []},
                          {"start": 2, "note": "Ответ", "items": [unit]}]}
        gate = {"status": "можно частично", "rationale": "Требуется условие ответа.", "evidence": [2]}
        whole = {"dimensions": [{"criterion": axis, "assessment": "Оценён ответ сервиса.", "evidence": [2]}
                                for axis in AXES], "development": gate, "testing": gate, "reconciled": [],
                 "findings": [{"title": "Условие", "category": "нарушение",
                               "triage": {"rule": "implementation_or_contract", "witness": []},
                               "criterion": "полнота", "reason": "Не задано событие.", "impact": "Сценарий не определён.",
                               "owner": "Аналитик", "question": "Когда вернуть OK?", "action": "Уточнить событие.",
                               "requirements": ["R-001"], "candidates": [], "evidence": [2]}]}

        def complete(prompt, model, schema):
            name = next(iter(schema["properties"]))
            if name == "dimensions":
                entry, = json.loads(prompt.rsplit("\n", 1)[1])["registry"]
                self.assertEqual(entry["quote"], ref["quote"])
                self.assertNotIn("statement", entry)
            if name == "checks":
                payload = json.loads(prompt.rsplit("\n", 1)[1])
                self.assertEqual(payload["source"], source)
                self.assertEqual(payload["review_questions"], [{"question": "Когда вернуть OK?",
                                                                 "evidence": [ref]}])
            qa = {"checks": [], "skipped": [{"axis": axis, "reason": "Только безусловный ответ."}
                                             for axis in lifecycle.AXES]}
            return deepcopy({"rows": local, "dimensions": whole, "conflicts": {"conflicts": []},
                             "checks": qa}[name]), {}

        with tempfile.TemporaryDirectory() as temp:
            with patch("reviewer.codex_provider.complete", side_effect=complete) as provider:
                result, attempts = analyze(source, lines, "demo", Path(temp), "digest")
                self.assertEqual(provider.call_count, 4)
                second, restored = analyze(source, lines, "demo", Path(temp), "digest")
                self.assertEqual(provider.call_count, 4)
                self.assertTrue(all(a["restored"] for a in restored))
                self.assertEqual(second, result)
        self.assertEqual(result["registry"][0]["original_id"], "X-1")
        run = dict(started_utc="2026-11-26", input_lines=2, input_name="test.md", input_sha256="x",
                   provider="test", model="test", elapsed_seconds=1, agent_version="test", usage={}, attempts=attempts)
        report = dict(schema_version=2, scope="Проверка", run=run, source=source, **result)
        html = render_report(report)
        self.assertIn("Основание приоритета", html)
        self.assertEqual(report["whole"]["findings"][0]["priority"], "существенный")
        changed = deepcopy(report)
        changed["whole"]["findings"][0]["priority"] = "редакционный"
        with self.assertRaisesRegex(ValueError, "приоритет расходится"):
            render_report(changed)
        for anchor in ["R-001","D-01","source-line-2","quality","register","coverage"]:
            self.assertIn('id="' + anchor + '"', html)
        damaged = deepcopy(report)
        damaged["segments"].pop()
        with self.assertRaises(ValueError):
            render_report(damaged)
        damaged = deepcopy(report)
        damaged["whole"]["findings"].clear()
        with self.assertRaisesRegex(ValueError, "без решения"):
            render_report(damaged)

    def test_table_identity_and_missing_parameter_review(self):
        lines = {1: "| Parameter | Type |", 3: "| amount | integer |"}
        self.assertEqual(original_id("| Transfer | PAY-20 |", "| Name | Requirement ID |"), "PAY-20")
        with self.assertRaises(ValueError):
            check_batch({"rows": [{"start": 3, "items": []}]},
                        [{"start": 3, "end": 3, "header": 1, "kind": "строка таблицы"}], lines)

    def test_fabricated_anchor_is_rejected_even_with_correct_start(self):
        row = {"start": 1, "items": [{"evidence": {"line": 1, "quote": "нет"}}]}
        with self.assertRaises(ValueError):
            check_batch({"rows": [row]}, [{"start":1, "end":1, "kind":"текст"}], {1:"да"})
