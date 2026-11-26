import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from reviewer.__main__ import run_usage
from reviewer.full import call_stage
from reviewer.contract import REVIEW


class CheckpointUsageTest(unittest.TestCase):
    def test_history_and_current_calls_are_reported_separately(self):
        attempts = [{"restored": True, "usage": {"input_tokens": 800}},
                    {"status": "rejected", "usage": {"input_tokens": 12}},
                    {"status": "accepted", "usage": {"input_tokens": 17}}]
        self.assertEqual(run_usage(attempts), {"usage": {"input_tokens": 829},
                         "current_usage": {"input_tokens": 29}, "fresh_calls": 2, "restored_attempts": 1})
        attempts[0]["usage"] = None
        self.assertIsNone(run_usage(attempts)["usage"])
        self.assertEqual(run_usage(attempts)["current_usage"], {"input_tokens": 29})

    def test_damaged_stage_is_recomputed_then_reused(self):
        with TemporaryDirectory() as directory, patch("reviewer.codex_provider.complete",
                  return_value=({"conflicts": []}, {"output_tokens": 11})) as provider:
            def run():
                _, attempts = call_stage("Сценарии", "prompt", [], REVIEW, lambda result: None,
                                         "test-model", Path(directory), "build")
                return run_usage(attempts)

            self.assertEqual(run()["fresh_calls"], 1)
            checkpoint, = Path(directory).glob("*.json")
            for corrupt in ("{", json.dumps({"result": {"conflicts": []}, "attempts": []})):
                checkpoint.write_text(corrupt)
                self.assertEqual(run()["current_usage"], {"output_tokens": 11})
                cached = run()
                self.assertEqual((cached["fresh_calls"], cached["current_usage"]), (0, {}))
                self.assertEqual(cached["usage"], {"output_tokens": 11})
            self.assertEqual(provider.call_count, 3)
