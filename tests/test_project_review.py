from email.message import Message
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import quote
from urllib.request import HTTPSHandler, build_opener
from urllib.response import addinfourl

from reviewer.project_review import ORIGIN, project_worker
from reviewer.serve import ROOT, SafeWorkerError


class ProjectService(HTTPSHandler):
    """Exercise real urllib cookies, redirect policy, encoding and error handlers offline."""
    def __init__(self):
        super().__init__()
        self.calls = []
        self.visits = 0
        self.report = (ROOT / "eval/fixed-acceptance/results/recorder-v1.json").read_bytes()
        self.identifier = "a" * 32
        self.submission_status = 202
        self.job_state = "done"

    def https_open(self, req):
        self.calls.append((req.full_url, dict(req.header_items()), req.data))
        headers = Message()
        code = 200
        path = req.selector
        if path == "/":
            self.visits += 1
            headers["Set-Cookie"] = f"__Host-razbor=visit{self.visits}; Path=/; Secure; HttpOnly; SameSite=Strict"
            mime, raw = "text/html", b"home"
        elif path == "/api/runs?mode=full":
            mime, raw, code = "application/json", json.dumps({"id": self.identifier}).encode(), self.submission_status
            if code == 307:
                headers["Location"] = "https://other.example/new"
        elif path == "/api/status/" + self.identifier:
            raw = json.dumps({"mode": "full", "state": self.job_state, "progress": [], "error": "Квота модели."}).encode()
            mime = "application/json"
        elif path == "/reports/" + self.identifier + "/review.json":
            mime, raw = "application/json", self.report
        else:
            raise AssertionError(req.full_url)
        headers["Content-Type"] = mime
        response = addinfourl(BytesIO(raw), headers, req.full_url, code)
        response.msg = "test"
        return response


class ProjectReviewTests(TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.source = ROOT / "eval/fixed-acceptance/recorder-v1.md"
        self.service = ProjectService()
        factory = patch("reviewer.project_review.build_opener",
                        side_effect=lambda *handlers: build_opener(*handlers, self.service))
        factory.start()
        self.addCleanup(factory.stop)

    def review(self, name, source=None):
        out = self.root / name
        out.mkdir()
        project_worker("full", source or self.source, None, out / "review.json", out / "review.html", None, lambda m: None)
        return out

    def test_posts_original_bytes_to_fixed_host_and_never_reuses_a_visit(self):
        name = "Энергия & приёмка.md"
        source = self.root / name
        source.write_bytes(self.source.read_bytes())
        report = json.loads(self.service.report)
        report["run"]["input_name"] = name
        self.service.report = json.dumps(report, ensure_ascii=False).encode()
        for i in (1, 2):
            out = self.review(str(i), source)
            calls = self.service.calls[-4:]
            self.assertIsNone(calls[0][1].get("Cookie"))
            self.assertTrue(all(url.startswith(ORIGIN + "/") for url, _, _ in calls))
            self.assertEqual([h.get("Cookie") for _, h, _ in calls[1:]], [f"__Host-razbor=visit{i}"] * 3)
            _, submitted, raw = calls[1]
            self.assertEqual(raw, source.read_bytes())
            self.assertEqual(submitted["Origin"], ORIGIN)
            self.assertEqual(submitted["X-file-name"], quote(name))
            self.assertNotIn("Authorization", submitted)
            self.assertEqual((out / "review.json").read_bytes(), self.service.report)
            self.assertIn("33 Вт·ч", (out / "review.html").read_text())

    def test_refuses_other_input_incomplete_review_and_wrong_name_before_publishing(self):
        original = json.loads(self.service.report)
        for key, change in [("sha", lambda r: r["run"].update(input_sha256="0" * 64)),
                            ("lines", lambda r: r["source"][0].update(text="Другое ТЗ")),
                            ("partial", lambda r: r.update(report_type="recheck")),
                            ("name", lambda r: r["run"].update(input_name="recorder.pdf"))]:
            with self.subTest(key=key):
                r = json.loads(json.dumps(original))
                change(r)
                self.service.report = json.dumps(r).encode()
                with self.assertRaisesRegex(SafeWorkerError, "локальную проверку"):
                    self.review(key)
                self.assertFalse((self.root / key / "review.json").exists())
                self.assertFalse((self.root / key / "review.html").exists())

    def test_quota_redirect_and_remote_failure_cannot_trigger_a_second_submission(self):
        for code, state, error in [(429, "done", "лимит"), (307, "done", "HTTP 307"), (202, "failed", "Квота модели")]:
            self.service.submission_status, self.service.job_state = code, state
            self.service.calls.clear()
            with self.subTest(code=code), self.assertRaisesRegex(SafeWorkerError, error):
                self.review(str(code))
            self.assertEqual(sum(raw is not None for _, _, raw in self.service.calls), 1)
            self.assertTrue(all(u.startswith(ORIGIN + "/") for u, _, _ in self.service.calls))
