from unittest import TestCase

from reviewer.timing import profile


class TimingProfileTests(TestCase):
    def test_overlapping_boundaries_and_restored_attempts(self):
        def call(stage, start, length, status="accepted"):
            return {"stage": stage, "started_utc": f"2026-11-27T00:00:{start:02d}+00:00",
                    "seconds": length, "status": status}

        a = call("Блоки 1–2", 0, 5, "rejected")
        b = call("Блоки 3–4", 2, 5)
        c = call("Сценарии QA", 7, 2)
        result = profile({"elapsed_seconds": 12, "attempts": [a, b, c, dict(c, restored=True)]})
        self.assertEqual((result["fresh_calls"], result["restored_attempts"], result["peak_concurrency"]),
                         (3, 1, 2))
        self.assertEqual(result["model_active_seconds"], 9)
        self.assertEqual(result["stages"][0], {"stage": "Блоки", "calls": 2, "rejected": 1, "failed": 0,
                                              "call_seconds": 10, "active_seconds": 7})

    def test_untimed_legacy_and_cached_runs(self):
        old = profile({"elapsed_seconds": 8, "attempts": [{"stage": "Весь документ", "status": "accepted",
                                                          "seconds": 7.2}]})
        self.assertIsNone(old["model_active_seconds"])
        self.assertIsNone(old["stages"][0]["active_seconds"])
        self.assertEqual(old["stages"][0]["call_seconds"], 7.2)
        simple = profile({"elapsed_seconds": 9, "attempts": [{"status": "accepted"}]})
        self.assertIsNone(simple["stages"][0]["call_seconds"])
        cached = profile({"elapsed_seconds": 0.2, "attempts": [{"restored": True}]})
        self.assertEqual((cached["model_active_seconds"], cached["peak_concurrency"]), (0, 0))
