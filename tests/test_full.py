from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reviewer.full import AXES, analyze, check_batch, check_critical_sources, local_batches, original_id
from reviewer.render import render_report


class FullReviewTest(unittest.TestCase):
    def test_balancing_preserves_order_and_handles_oversize_sections(self):
        blocks = [{"start": i, "end": i} for i in range(1, 124)]
        source = {i: "x" * (4000 if i == 42 else 35) for i in range(1, 124)}
        groups = list(local_batches(blocks, source))
        self.assertEqual([b for group in groups for b in group], blocks)
        self.assertTrue(all(0 < len(group) <= 26 for group in groups))
        self.assertEqual(list(local_batches([], {})), [])

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
        ref = {"line": 2, "quote": "Вернуть OK."}
        unit = {"role": "требование", "statement": "Вернуть OK", "evidence": ref,
                "status": "нужно уточнение", "assessment": "Какое условие запускает ответ?"}
        local = {"rows": [{"start": 1, "note": "Название", "items": []},
                          {"start": 2, "note": "Ответ", "items": [unit]}]}
        gate = {"status": "можно частично", "rationale": "Требуется условие ответа.", "evidence": [ref]}
        whole = {"dimensions": [{"criterion": axis, "assessment": "Оценён ответ сервиса.", "evidence": [ref]}
                                for axis in AXES], "development": gate, "testing": gate, "reconciled": [],
                 "findings": [{"title": "Условие", "category": "нарушение",
                               "triage": {"rule": "implementation_or_contract", "witness": []},
                               "criterion": "полнота", "reason": "Не задано событие.", "impact": "Сценарий не определён.",
                               "owner": "Аналитик", "question": "Когда вернуть OK?", "action": "Уточнить событие.",
                               "requirements": ["R-001"], "candidates": [], "evidence": [ref]}]}

        def complete(prompt, model, schema):
            name = next(iter(schema["properties"]))
            return deepcopy({"rows": local, "dimensions": whole, "conflicts": {"conflicts": []}}[name]), {}

        with tempfile.TemporaryDirectory() as temp:
            with patch("reviewer.codex_provider.complete", side_effect=complete) as provider:
                result, attempts = analyze(source, lines, "demo", Path(temp), "digest")
                self.assertEqual(provider.call_count, 3)
                second, restored = analyze(source, lines, "demo", Path(temp), "digest")
                self.assertEqual(provider.call_count, 3)
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
