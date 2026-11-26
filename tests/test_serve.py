import http.client
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
import time
import unittest
from urllib.parse import quote

from reviewer.serve import Workbench, WorkbenchServer, execute


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

    def worker(self, mode, document, baseline, json_report, html_report, model, progress):
        self.calls.append((mode, document.name, document.read_bytes(), baseline, model))
        if document.name == "failure.md":
            raise RuntimeError("provider stderr contains secret-token")
        progress("Весь документ · проверено")
        self.entered.set()
        if not self.release.wait(2):
            raise AssertionError("Worker waited for release")
        json_report.write_text('{"review":true}')
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
        self.assertEqual(self.calls[0], ("full", "требования.md", b"# Document", None, "test-model"))
        for baseline in ("demo", identifier):
            code, body, _ = self.upload(f"/api/runs?mode=recheck&baseline={baseline}", name="rev.txt")
            self.assertEqual(code, 202)
            recheck_id = json.loads(body)["id"]
            self.assertEqual(self.wait_until_done(recheck_id)["mode"], "recheck")
            path = self.demo if baseline == "demo" else self.app.storage / baseline
            self.assertEqual(self.calls[-1][3], path / "review.json")
            self.assertEqual(self.upload(f"/api/runs?mode=recheck&baseline={recheck_id}")[0], 409)

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


class CliOutputTest(unittest.TestCase):
    def test_only_fixed_cli_output_can_reach_client(self):
        progress = []
        command = [sys.executable, "-c",
                   "print('Весь документ · проверено'); print('key=xyz'); "
                   "print('Codex CLI не найден: secret'); raise SystemExit(1)"]
        with self.assertRaisesRegex(RuntimeError, "^Установите Codex CLI перед запуском проверки\\.$"):
            execute(command, progress.append, "Ошибка")
        self.assertEqual(progress, ["Весь документ · проверено"])
