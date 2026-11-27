from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from reviewer.render import render_report
from reviewer.recheck import render_html
from reviewer.serve import ROOT, Workbench, demo_directory


class SavedOwnerExample(unittest.TestCase):
    def test_example_matches_demo_and_renders_both_turns_without_changing_live_discussion(self):
        baseline = demo_directory(ROOT) / "review.json"
        sample = baseline.parent / "owner"
        digest = sha256(baseline.read_bytes()).hexdigest()
        with TemporaryDirectory() as directory:
            app = Workbench(Path(directory))
            self.assertEqual(app.saved_example("demo", digest)["finding_id"], "D-01")
            self.assertIsNone(app.saved_example("demo", "0" * 64))
            self.assertIsNone(app.saved_example("own-upload", digest))
            self.assertEqual(app.conversations("demo", digest), [])
        record = json.loads((sample / "dialogue.json").read_bytes())
        self.assertEqual(record["baseline_sha256"], digest)
        self.assertEqual([t["state"] for t in record["turns"]], ["needs_followup", "ready"])
        html = render_report(record)
        self.assertIn("Ответ 2", html)
        self.assertIn("Замечание открыто", html)
        self.assertEqual(html, (sample / "dialogue.html").read_text())
        checked = json.loads((sample / "wrong-revision.json").read_bytes())
        self.assertEqual(checked["checks"][0]["clarification"]["transcript"], record)
        self.assertEqual(checked["checks"][0]["workflow_status"], "открыто")
        self.assertIn("противоречит решению", render_html(checked))
