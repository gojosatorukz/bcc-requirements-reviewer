from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from reviewer.full import AXES, analyze, check_batch
from reviewer.render import render_report


class FullReviewTest(unittest.TestCase):
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
                 "findings": [{"title": "Условие", "category": "нарушение", "priority": "существенный",
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

    def test_fabricated_anchor_is_rejected_even_with_correct_start(self):
        row = {"start": 1, "items": [{"evidence": {"line": 1, "quote": "нет"}}]}
        with self.assertRaises(ValueError):
            check_batch({"rows": [row]}, [{"start":1, "end":1, "kind":"текст"}], {1:"да"})
