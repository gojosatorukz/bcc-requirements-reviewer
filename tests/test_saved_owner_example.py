from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from reviewer.render import render_report
from reviewer.dialogue_view import render_with_followup
from reviewer.recheck import render_html
from reviewer.serve import ROOT, Workbench, demo_directory


class SavedOwnerExample(unittest.TestCase):
    def test_jury_report_takes_precedence_when_its_json_and_html_are_ready(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "runs" / "bcc-release"
            archive.mkdir(parents=True)
            (archive / "review.json").write_text("{}")
            (archive / "review.html").write_text("<title>Прошлый прогон</title>")
            self.assertEqual(demo_directory(root), archive)

            jury = root / "runs" / "bcc-jury"
            jury.mkdir()
            (jury / "review.json").write_text("{}")
            self.assertEqual(demo_directory(root), archive)

            (jury / "review.html").write_text("<title>Прогон для жюри</title>")
            self.assertEqual(demo_directory(root), jury)

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

    def test_embedded_followup_is_bound_to_the_baseline_conversation_and_revision(self):
        release = ROOT / "runs/bcc-jury"
        raw = (release / "review.json").read_bytes()
        owner_path = release / "owner/dialogue.json"
        revision_path = release / "owner/wrong-revision.json"
        html = render_with_followup(raw, owner_path, revision_path)
        self.assertEqual(html.encode(), (release / "review.html").read_bytes())
        self.assertIn("замечание открыто", html)
        for turn in json.loads(owner_path.read_bytes())["turns"]:
            self.assertIn(turn["question"], html)
            self.assertIn(turn["answer"], html)
        with self.assertRaises(ValueError):
            render_with_followup(raw + b"\n", owner_path, revision_path)
        with TemporaryDirectory() as directory:
            changed = Path(directory) / "revision.json"
            revision = json.loads(revision_path.read_bytes())
            revision["checks"][0]["clarification"]["transcript"]["rule"] = "Другое согласование"
            changed.write_text(json.dumps(revision))
            with self.assertRaises(ValueError):
                render_with_followup(raw, owner_path, changed)
