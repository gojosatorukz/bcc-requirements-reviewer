import http.client
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote

from reviewer.serve import CROSS_CHECK, Run, Workbench, WorkbenchServer, cli_worker, execute


class WorkbenchTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "reviewer").mkdir()
        (self.root / "reviewer" / "workbench.html").write_text("<title>Рабочее место</title>")
        self.demo = self.root / "runs" / "bcc-final"
        self.demo.mkdir(parents=True)
        (self.demo / "review.html").write_text("<title>Demo</title>")
        (self.demo / "review.json").write_text('{"demo":true}')
        self.entered = Event()
        self.release = Event()
        self.calls = []
        self.app = Workbench(self.root / "local", root=self.root, worker=self.worker, model="test-model")
        try:
            self.server = WorkbenchServer(0, self.app)
        except PermissionError:
            self.skipTest("Local server sockets are not permitted in this environment")
        thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.app.wait)
        self.addCleanup(self.release.set)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"

    def worker(self, mode, document, baseline, json_report, html_report, model, progress, findings=(),
               decisions=(), baseline_sha256=None):
        self.calls.append((mode, document.name, document.read_bytes(), baseline, model, findings))
        self.decision_args = decisions, baseline_sha256
        if document.name == "failure.md":
            raise RuntimeError("provider stderr contains secret-token")
        progress("Весь документ · проверено")
        self.entered.set()
        if not self.release.wait(2):
            raise AssertionError("Worker waited for release")
        json_report.write_text('{"schema_version":2,"whole":{"findings":[{"id":"D-03"}]}}')
        html_report.write_text("<title>Report</title>")

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def upload(self, path="/api/runs?mode=full", name="требования.md", body=b"# Document", **headers):
        return self.request("POST", path, body, {"Origin": self.origin, "X-File-Name": quote(name), **headers})

    def status(self, identifier):
        code, data, _ = self.request("GET", f"/api/status/{identifier}")
        self.assertEqual(code, 200)
        return json.loads(data)

    def wait_until_done(self, identifier):
        deadline = time.monotonic() + 3
        while True:
            result = self.status(identifier)
            if result["state"] in {"done", "failed"}:
                return result
            if time.monotonic() > deadline:
                self.fail(f"Run did not finish: {result}")
            time.sleep(0.01)

    def test_demo_fallback_and_immutable_modes(self):
        benchmark = self.demo.with_name("bcc-benchmark")
        benchmark.mkdir()
        (benchmark / "review.json").write_text('{"benchmark":true}')
        code, page, _ = self.request("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn("Рабочее место", page.decode())
        code, body, headers = self.request("GET", "/reports/demo/review.json?download=1")
        self.assertEqual((code, json.loads(body)), (200, {"demo": True}))
        self.assertEqual(headers["Content-Disposition"], 'attachment; filename="review.json"')
        (benchmark / "review.html").write_text("<title>Benchmark</title>")
        self.assertIn(b"Benchmark", self.request("GET", "/reports/demo/review.html")[1])
        latest = self.demo.with_name("bcc-priority")
        latest.mkdir()
        (latest / "review.json").write_text('{"priority_policy":"impact-v1"}')
        self.assertIn(b"Benchmark", self.request("GET", "/reports/demo/review.html")[1])
        (latest / "review.html").write_text("<title>Impact</title>")
        self.assertIn(b"Impact", self.request("GET", "/reports/demo/review.html")[1])
        submitted = self.demo.with_name("bcc-submission")
        submitted.mkdir()
        (submitted / "review.json").write_text('{"current":true}')
        self.assertIn(b"Impact", self.request("GET", "/reports/demo/review.html")[1])
        (submitted / "review.html").write_text("<title>Submitted run</title>")
        self.assertIn(b"Submitted run", self.request("GET", "/reports/demo/review.html")[1])
        self.assertEqual(self.request("GET", "/reports/demo/../review.json")[0], 404)
        self.assertEqual(self.calls, [])

    def test_gallery_serves_only_the_bundled_assets(self):
        directory = self.root / "design" / "concepts"
        directory.mkdir(parents=True)
        for name in ("index.html", "gallery.css", "case.js", "gallery.js", "private.txt"):
            (directory / name).write_text(name)
        for name in ("index.html", "gallery.css", "case.js", "gallery.js"):
            status, body, _ = self.request("GET", f"/design/concepts/{name}")
            self.assertEqual((status, body), (200, name.encode()))
        self.assertEqual(self.request("GET", "/design/concepts/private.txt")[0], 404)

    def test_full_and_recheck_run_paths(self):
        code, body, _ = self.upload()
        self.assertEqual(code, 202)
        identifier = json.loads(body)["id"]
        self.assertTrue(self.entered.wait(1))
        progress = self.status(identifier)
        self.assertEqual(progress, {"state": "running", "mode": "full", "error": "",
                                    "progress": ["Весь документ · проверено"]})
        self.assertEqual(self.request("GET", f"/reports/{identifier}/review.json")[0], 409)
        self.assertEqual(self.upload()[0], 409)
        self.release.set()
        self.assertEqual(self.wait_until_done(identifier)["state"], "done")
        self.assertEqual(self.request("GET", f"/reports/{identifier}/review.json")[0], 200)
        self.assertEqual(self.calls[0], ("full", "требования.md", b"# Document", None, "test-model", ()))
        for baseline in ("demo", identifier):
            code, body, _ = self.upload(f"/api/runs?mode=recheck&baseline={baseline}", name="rev.txt")
            self.assertEqual(code, 202)
            recheck_id = json.loads(body)["id"]
            self.assertEqual(self.wait_until_done(recheck_id)["mode"], "recheck")
            path = self.demo if baseline == "demo" else self.app.storage / baseline
            self.assertEqual(self.calls[-1][3], path / "review.json")
            self.assertEqual(self.calls[-1][-1], ())
            self.assertEqual(self.upload(f"/api/runs?mode=recheck&baseline={recheck_id}")[0], 409)

    def test_selected_findings_are_validated_against_the_chosen_baseline(self):
        (self.demo / "review.json").write_text(
            '{"schema_version":2,"whole":{"findings":[{"id":"D-01"},{"id":"D-02"}]}}')
        self.release.set()
        code, body, _ = self.upload()
        self.assertEqual(code, 202)
        previous = json.loads(body)["id"]
        self.assertEqual(self.wait_until_done(previous)["state"], "done")
        demo = "/api/runs?mode=recheck&baseline=demo"
        user = f"/api/runs?mode=recheck&baseline={previous}"
        for path in (demo + "&finding=D-03", user + "&finding=D-01", demo + "&finding=",
                     demo + "&finding=../../D-01", demo + "&finding=D-01&finding=D-01",
                     "/api/runs?mode=full&finding=D-01"):
            with self.subTest(path=path):
                self.assertEqual(self.upload(path)[0], 400)
        self.assertEqual(len(self.calls), 1)
        for path, expected in ((demo + "&finding=D-02&finding=D-01", ("D-02", "D-01")),
                               (user + "&finding=D-03", ("D-03",))):
            code, body, _ = self.upload(path)
            self.assertEqual(code, 202)
            self.assertEqual(self.wait_until_done(json.loads(body)["id"])["state"], "done")
            self.assertEqual(self.calls[-1][-1], expected)

    def test_decisions_are_bound_to_report_bytes_and_selected_findings(self):
        path = self.demo / "review.json"
        finding = {"id": "D-01", "priority": "критический", "category": "нарушение",
                   "triage": {"witness": [{"first": {"outcome": "A"}, "second": {"outcome": "B"}}]}}
        path.write_text(json.dumps({"schema_version": 2, "whole": {"findings": [finding, {"id": "D-02"}]}}))
        code, body, headers = self.request("GET", "/reports/demo/review.json")
        self.assertEqual(code, 200)
        digest = sha256(body).hexdigest()
        self.assertEqual(headers["X-Report-SHA256"], digest)
        base = "/api/runs?mode=recheck&baseline=demo"
        valid = "&decision=D-01%3Dfirst&baseline_sha256=" + digest
        for invalid in ("&finding=D-02" + valid, valid + "&decision=D-01%3Dsecond", "&decision=D-01%3Dfirst"):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.upload(base + invalid)[0], 400)
        path.write_bytes(body + b"\n")
        self.assertEqual(self.upload(base + valid)[0], 409)
        self.assertEqual(self.calls, [])
        path.write_bytes(body)
        self.release.set()
        code, body, _ = self.upload(base + valid)
        self.assertEqual(code, 202)
        self.assertEqual(self.wait_until_done(json.loads(body)["id"])["state"], "done")
        self.assertEqual(self.decision_args, (("D-01=first",), digest))

    def test_validates_origin_host_names_and_size(self):
        self.assertEqual(self.upload(Origin="http://127.0.0.1.evil.invalid")[0], 403)
        self.assertEqual(self.upload(Host=f"attacker.invalid:{self.server.server_port}")[0], 403)
        self.assertEqual(self.request("POST", "/api/runs?mode=full", b"data", {"X-File-Name": "input.md"})[0], 403)
        self.assertEqual(self.upload(name="../outside.md")[0], 400)
        self.assertEqual(self.upload(name="dir\\file.md")[0], 400)
        self.assertEqual(self.upload(name="example.html")[0], 415)
        self.assertEqual(self.upload(body=b"")[0], 400)
        self.assertEqual(self.upload(body=b"x" * 120_001)[0], 413)
        self.assertEqual(self.upload("/api/runs?mode=full&baseline=demo")[0], 400)
        self.assertEqual(self.upload("/api/runs?mode=recheck&baseline=..")[0], 400)
        self.assertEqual(self.calls, [])

    def test_failed_worker_does_not_expose_exception_or_reports(self):
        code, body, _ = self.upload(name="failure.md")
        self.assertEqual(code, 202)
        identifier = json.loads(body)["id"]
        status = self.wait_until_done(identifier)
        self.assertEqual(status["state"], "failed")
        self.assertNotIn("secret-token", status["error"])
        self.assertEqual(self.request("GET", f"/reports/{identifier}/review.html")[0], 409)


class FindingArgumentsTest(unittest.TestCase):
    @patch("reviewer.serve.execute")
    def test_owner_decisions_pass_sha_to_cli(self, execute_mock):
        digest = "a" * 64
        cli_worker("recheck", Path("updated.md"), Path("baseline.json"), Path("review.json"), Path("review.html"),
                   None, lambda text: None, decisions=("D-01=first", "D-02=second"), baseline_sha256=digest)
        self.assertEqual(execute_mock.call_args_list[0].args[0][-6:],
                         ["--decision", "D-01=first", "--decision", "D-02=second", "--baseline-sha256", digest])

    @patch("reviewer.serve.execute")
    def test_recheck_passes_repeated_finding_flags_to_cli(self, execute_mock):
        cli_worker("recheck", Path("updated.md"), Path("baseline.json"),
                   Path("review.json"), Path("review.html"), None, lambda text: None, ("D-03", "D-01"))
        command = execute_mock.call_args_list[0].args[0]
        self.assertEqual(command[1:], ["-m", "reviewer.recheck", "baseline.json", "updated.md", "review.json",
                                      "--finding", "D-03", "--finding", "D-01"])


class CliOutputTest(unittest.TestCase):
    def test_coverage_survives_parallel_order_duplicates_and_a_truncated_log(self):
        run = Run("example", "full", Path("unused"))
        run.record("План проверки · групп строк: 104")
        for number in reversed(range(104)):
            run.record(f"Блоки {number+1}–{number+1} · проверено")
        run.record("Блоки 1–1 · проверена сохранённая стадия")
        self.assertEqual(len(run.progress), 100)
        self.assertEqual((len(run.checked_groups), run.group_total, run.phase), (104, 104, "groups"))
        run.record(CROSS_CHECK)
        self.assertEqual(run.phase, "whole")
        run.record("Весь документ · проверено")
        self.assertEqual(run.phase, "render")

    def test_only_fixed_cli_output_can_reach_client(self):
        progress = []
        command = [sys.executable, "-c",
                   "print('План проверки · групп строк: 3'); print('План проверки · групп строк: secret'); "
                   "print('Сверка требований · начата'); print('Весь документ · проверено'); print('key=xyz'); "
                   "print('Codex CLI не найден: secret'); raise SystemExit(1)"]
        with self.assertRaisesRegex(RuntimeError, "^Установите Codex CLI перед запуском проверки\\.$"):
            execute(command, progress.append, "Ошибка")
        self.assertEqual(progress, ["План проверки · групп строк: 3", CROSS_CHECK, "Весь документ · проверено"])
