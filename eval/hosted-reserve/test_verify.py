from copy import deepcopy
from importlib import import_module
import unittest


audit = import_module("eval.hosted-reserve.verify")


class FrozenAcceptanceTests(unittest.TestCase):
    def test_passport_requires_fresh_accepted_call_gemini_quota_and_new_run(self):
        stamp = "2026-11-27T12:00:00+00:00"
        run = {"model": "@cf/example", "provider": "cloudflare-workers-ai", "fresh_calls": 1,
               "attempts": [{"status": "accepted"}], "started_utc": stamp,
               "fallback_from": {"provider": "gemini-api", "reason": "HTTP 429", "model": "gemini-example"}}
        receipt = {"started_utc": stamp, "downloaded_utc": stamp, "route": "local",
                   "endpoint": "/api/project-runs", "submit_status": 202, "job_state": "done", "execution": "project",
                   "upload_sha256": audit.HASHES["local"], "checkout_revision": "a" * 40}
        audit.passport(run, receipt, "local")
        for edit in ({"provider": "gemini-api"}, {"fallback_from": {}}, {"started_utc": "2024-11-27T12:00:00Z"},
                     {"attempts": [{"restored": True, "status": "accepted"}]}, {"attempts": [{"status": "rejected"}]}):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                audit.passport({**run, **edit}, receipt, "local")
        with self.assertRaises(ValueError):
            audit.passport(run, {**receipt, "execution": None}, "local")

    def test_quality_requires_both_grounded_outcomes_and_does_not_confuse_the_two_thresholds(self):
        finding = {"id": "D-01", "category": "нарушение", "evidence": [{"line": 4}, {"line": 11}],
                   "question": "При 23 мм печатать «Клеевой участок» или «Швейный участок»?"}
        self.assertTrue(audit.grounded_quality([finding], 23)["passed"])
        self.assertFalse(audit.grounded_quality([finding], 18)["passed"])
        weak = {**finding, "question": "Как печатать талон при 23 мм?"}
        self.assertFalse(audit.grounded_quality([weak], 23)["passed"])
        false_control = deepcopy(finding)
        false_control["evidence"][1]["line"] = 10
        self.assertFalse(audit.grounded_quality([finding, false_control], 23)["passed"])
