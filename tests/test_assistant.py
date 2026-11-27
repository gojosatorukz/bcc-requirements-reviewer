import json
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from reviewer.assistant import MAX_QUESTION, READ_ONLY, UNKNOWN, passages, respond
from reviewer.full import InvalidStageResult
from reviewer.serve import QUOTA_MESSAGE, ROOT, Run, Workbench, WorkbenchError


BASE = ROOT / "eval/external-bowling/review.json"
RECHECK = ROOT / "runs/bcc-release/owner/wrong-revision.json"


class AssistantTest(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.demo = self.root / "runs/bcc-release"
        self.demo.mkdir(parents=True)
        self.raw = BASE.read_bytes()
        (self.demo / "review.json").write_bytes(self.raw)
        (self.demo / "review.html").write_text("<html></html>")
        self.report = json.loads(self.raw)
        self.sha = sha256(self.raw).hexdigest()
        self.app = Workbench(self.root / "jobs", root=self.root, model="test")
        self.row = self.report["whole"]["findings"][0]
        self.fid = self.row["id"]
        self.query = {"question": "Какой вопрос задать владельцу?", "focus": self.fid}
        self.reply = {"status": "answered", "answer": self.row["question"],
                      "sources": [{"anchor": self.fid, "quote": self.row["question"][:160]}]}

    def test_verified_excerpt_followup_links_and_report_isolation(self):
        with patch("reviewer.codex_provider.complete", return_value=(self.reply, {})) as complete:
            first = self.app.ask_assistant("demo", self.sha, self.query)
            self.assertEqual(first["sources"][0]["href"], f"/reports/demo/review.html?sha256={self.sha}#{self.fid}")
            second = self.app.ask_assistant("demo", self.sha, {"question": "Почему?", "previous": first["token"]})
            self.assertIn(self.query["question"], complete.call_args.args[0])
            self.assertNotEqual(first["token"], second["token"])
            imported = self.app.import_report(self.raw)
            with self.assertRaises(WorkbenchError) as error:
                self.app.ask_assistant(imported["id"], self.sha, {**self.query, "previous": second["token"]})
            self.assertEqual(error.exception.code, 410)
            with self.assertRaises(WorkbenchError) as error:
                self.app.ask_assistant("demo", self.sha, {**self.query, "previous": first["token"]})
            self.assertEqual(error.exception.code, 410)
            own = self.app.ask_assistant(imported["id"], self.sha, self.query)
            self.assertTrue(own["sources"][0]["href"].startswith("/reports/" + imported["id"] + "/"))
            self.assertEqual((self.demo / "review.json").read_bytes(), self.raw)
            self.assertEqual(self.app.dialogues, {})

    def test_refuses_fabricated_source_excerpt_and_quoted_answer(self):
        for source, answer in [
            ({"anchor": "source-line-999999", "quote": "из источника"}, "Ответ"),
            ({"anchor": self.fid, "quote": "Вымышленная фраза."}, "Ответ"),
            (self.reply["sources"][0], "В ТЗ сказано «вымышленная фраза»."),
            (self.reply["sources"][0], "Как указано в строке 999999."),
            (self.reply["sources"][0], "См. D-999 или https://outside.invalid."),
        ]:
            with self.subTest(answer=answer, source=source), patch(
                "reviewer.codex_provider.complete", return_value=({"status": "answered", "answer": answer,
                                                                  "sources": [source]}, {})
            ), self.assertRaises(InvalidStageResult):
                respond(self.report, self.sha, "Что известно?", self.fid, [], "test")

    def test_insufficient_read_only_and_sanitized_failure(self):
        for status, expected in (("insufficient", UNKNOWN), ("read_only", READ_ONLY)):
            with patch("reviewer.codex_provider.complete", return_value=({"status": status, "answer": None, "sources": []}, {})):
                answer = self.app.ask_assistant("demo", self.sha, self.query)
                self.assertEqual((answer["answer"], answer["sources"]), (expected, []))
        with patch("reviewer.codex_provider.complete", side_effect=ValueError("provider-secret-text")):
            with self.assertRaises(WorkbenchError) as error:
                self.app.ask_assistant("demo", self.sha, self.query)
            self.assertEqual(error.exception.code, 502)
            self.assertNotIn("provider-secret-text", str(error.exception))

    def test_provider_limits_and_model_changes_do_not_leak_details(self):
        for detail, status in (("429 usage limit internal-account-tag", 429), ("quota_exhausted", 429),
                               ("model_not_found internal-account-tag", 502)):
            with patch("reviewer.codex_provider.complete", side_effect=ValueError(detail)), self.assertRaises(WorkbenchError) as error:
                self.app.ask_assistant("demo", self.sha, self.query)
            self.assertEqual(error.exception.code, status)
            self.assertNotIn("internal-account-tag", str(error.exception))
            if status == 429:
                self.assertEqual(str(error.exception), QUOTA_MESSAGE)
        with patch("reviewer.codex_provider.complete", return_value=(self.reply, {})):
            first = self.app.ask_assistant("demo", self.sha, self.query)
            self.app.model = "other"
            with self.assertRaises(WorkbenchError) as error:
                self.app.ask_assistant("demo", self.sha, {**self.query, "previous": first["token"]})
            self.assertEqual(error.exception.code, 410)

    def test_rejects_changed_baseline_during_call_and_bounds_requests(self):
        def switching(*args):
            (self.demo / "review.json").write_bytes(self.raw + b"\n")
            return self.reply, {}
        with patch("reviewer.codex_provider.complete", side_effect=switching), self.assertRaises(WorkbenchError) as error:
            self.app.ask_assistant("demo", self.sha, self.query)
        self.assertEqual(error.exception.code, 409)
        self.assertFalse(self.app.assistant_history)
        for payload in [{}, {"question": " "}, {"question": "a" * (MAX_QUESTION + 1)}, {**self.query, "api_key": "x"}]:
            with self.assertRaises(WorkbenchError) as error:
                self.app.ask_assistant("demo", self.sha, payload)
            self.assertEqual(error.exception.code, 400)
        with self.app.assistant_slots, self.app.assistant_slots, self.assertRaises(WorkbenchError) as error:
            self.app.ask_assistant("demo", self.sha, self.query)
        self.assertEqual(error.exception.code, 429)

    def test_recheck_distinguishes_original_issue_from_owner_rule(self):
        raw = RECHECK.read_bytes()
        report = json.loads(raw)
        item = report["checks"][0]
        source = passages(report)
        statement = item["clarification"]["conditions"][-1]["reason"]
        self.assertIn(statement, source[item["id"]].text)
        self.assertIn("Замечание открыто", source[item["id"]].text)
        self.assertIn("Исходная проблема в новой редакции: устранено", source[item["id"]].text)
        run = "b" * 32
        self.app.runs[run] = Run(run, "recheck", RECHECK.parent, state="done")
        with TemporaryDirectory() as job:
            self.app.runs[run].directory = Path(job)
            (Path(job) / "review.json").write_bytes(raw)
            answer = {"status": "answered", "answer": statement,
                      "sources": [{"anchor": item["id"], "quote": statement[:80]}]}
            with patch("reviewer.codex_provider.complete", return_value=(answer, {})):
                result = self.app.ask_assistant(run, sha256(raw).hexdigest(), {**self.query, "focus": item["id"]})
            self.assertIn(run + "/review.html", result["sources"][0]["href"])
