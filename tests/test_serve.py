import http.client
from concurrent.futures import ThreadPoolExecutor
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

from reviewer.serve import CROSS_CHECK, IMPORT_LIMIT, ROOT, Run, Workbench, WorkbenchServer, cli_worker, execute


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
               decisions=(), baseline_sha256=None, clarifications=()):
        self.calls.append((mode, document.name, document.read_bytes(), baseline, model, findings))
        self.decision_args = decisions, baseline_sha256
        self.clarifications = [json.loads(path.read_text()) for path in clarifications]
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

    def test_home_names_the_selected_model_before_submission(self):
        (self.root / "reviewer" / "workbench.html").write_text((ROOT / "reviewer" / "workbench.html").read_text())
        self.app.model = "Model A & B"
        self.assertIn("Model A &amp; B", self.request("GET", "/")[1].decode())
        self.app.model = None
        with patch.dict("os.environ", {"REVIEW_PROVIDER": "gemini", "REVIEW_MODEL": ""}):
            self.assertIn("gemini-3.5-flash-lite", self.request("GET", "/")[1].decode())

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

    def test_same_source_resumes_after_failure_and_runs_fresh_after_success(self):
        self.app.worker = cli_worker
        locations = []

        def staged(command, progress, failure):
            if "reviewer.render" in command:
                Path(command[-1]).write_text("<title>Review</title>")
                return
            cache = Path(command[command.index("--checkpoint-dir") + 1])
            locations.append(cache)
            if len(locations) == 1:
                cache.mkdir()
                (cache / "validated.json").write_text("{}")
                from reviewer.serve import SafeWorkerError
                raise SafeWorkerError("Провайдер недоступен.")
            Path(command[4]).write_text("{}")

        def run(name, content):
            code, body, _ = self.upload(name=name, body=content)
            self.assertEqual(code, 202)
            return self.wait_until_done(json.loads(body)["id"])

        with patch("reviewer.serve.execute", side_effect=staged):
            self.assertEqual(run("v1.md", b"old")["state"], "failed")
            self.assertEqual(run("v1.md", b"updated")["state"], "done")
            self.assertEqual(run("v1.txt", b"old")["state"], "done")
            resumed = run("copy.md", b"old")
            self.assertEqual(resumed["state"], "done")
            self.assertIn("Продолжаем с проверенных стадий предыдущей попытки", resumed["progress"])
            self.assertEqual(run("v1.md", b"old")["state"], "done")
        initial, changed, other_format, retried, fresh = locations
        self.assertEqual(initial, retried)
        self.assertEqual(len({initial, changed, other_format, fresh}), 4)

    def test_assistant_http_is_bound_to_the_import_and_verified_html(self):
        raw = (ROOT / "eval/external-bowling/review.json").read_bytes()
        record = json.loads(raw)
        fid, question = [record["whole"]["findings"][0][k] for k in ("id", "question")]
        imported = self.app.import_report(raw)
        route = f"/api/assistant/{imported['id']}/{imported['sha256']}"
        headers = {"Origin": self.origin, "Content-Type": "application/json"}
        body = json.dumps({"question": "О чём спросить?", "focus": fid}).encode()
        self.assertEqual(self.request("POST", route, body, {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(self.request("POST", route, b"[]", headers)[0], 400)
        self.assertEqual(self.request("POST", route, b"{", headers)[0], 400)
        self.assertEqual(self.request("POST", route, b" " * 24_001, headers)[0], 413)
        wrong = route.rsplit("/", 1)[0] + "/" + "f" * 64
        self.assertEqual(self.request("POST", wrong, body, headers)[0], 409)
        returned = {"status": "answered", "answer": question, "sources": [{"anchor": fid, "quote": question[:90]}]}
        with patch("reviewer.codex_provider.complete", return_value=(returned, {})):
            code, data, _ = self.request("POST", route, body, headers)
        self.assertEqual(code, 200)
        result = json.loads(data)
        href, anchor = result["sources"][0]["href"].split("#")
        code, html, _ = self.request("GET", href)
        self.assertEqual(code, 200)
        self.assertIn(f'id="{anchor}"', html.decode())
        self.assertEqual(self.request("GET", href.replace(imported["sha256"], "f" * 64))[0], 409)

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

    def discussion_fixture(self):
        path = self.demo / "review.json"
        items = [{"id": "D-01", "priority": "критический", "category": "нарушение",
                  "triage": {"witness": [{"first": {"outcome": "A"}, "second": {"outcome": "B"}}]}},
                 {"id": "D-08", "priority": "существенный"}]
        path.write_text(json.dumps({"schema_version": 2, "whole": {"findings": items}}))
        digest = sha256(path.read_bytes()).hexdigest()
        route = "/api/clarifications/demo/" + digest
        headers = {"Origin": self.origin, "Content-Type": "application/json"}

        def send(fid, answer, previous=None, **overrides):
            data = dict(finding_id=fid, answer=answer, previous=previous, **overrides)
            code, body, _ = self.request("POST", route, json.dumps(data), headers)
            return code, json.loads(body)

        return route, digest, send

    @patch("reviewer.clarify.clarify")
    def test_free_text_is_canonical_through_reload_stale_turns_and_scoped_recheck(self, clarify):
        route, digest, send = self.discussion_fixture()
        def reply(path, fid, answers, model, sha, *, prior):
            self.assertEqual((path, model, sha), (self.demo / "review.json", "test-model", digest))
            if len(answers) > 1:
                self.assertEqual(prior["answers"], answers[:-1])
            return dict(finding_id=fid, baseline_sha256=sha, answers=answers, rule="Итог.", follow_up="Какой месяц?",
                        state="needs_followup" if len(answers) == 1 else "ready")
        clarify.side_effect = reply
        self.assertEqual(send("D-01", "День покупки.", record={"state": "ready"})[0], 400)
        self.assertEqual(send("D-99", "Как обычно.")[0], 400)
        code, first = send("D-01", "Только после проведения.")
        self.assertEqual(code, 200)
        self.assertEqual(first["question"], "Какой месяц?")
        self.assertEqual(json.loads(self.request("GET", route)[1])["entries"], [first])
        self.assertEqual(send("D-01", "Уточнение со старой вкладки.")[0], 409)
        scope = "/api/runs?mode=recheck&baseline=demo"
        bound = "&baseline_sha256=" + digest
        self.release.set()
        def check(path):
            code, body, _ = self.upload(path)
            self.assertEqual(code, 202, body)
            self.assertEqual(self.wait_until_done(json.loads(body)["id"])["state"], "done")

        # An ordinary critical recheck automatically carries the official open discussion.
        check(scope)
        self.assertEqual(self.clarifications, [first["record"]])
        self.assertEqual(self.decision_args, ((), digest))
        self.assertEqual(self.upload(scope + bound + "&decision=D-01%3Dfirst")[0], 409)
        code, updated = send("D-01", "По месяцу покупки.", first["token"])
        self.assertEqual(code, 200)
        self.assertEqual(updated["question"], "Что нужно изменить в предложенном правиле «Итог.»?")
        self.assertNotEqual(updated["token"], first["token"])
        self.assertEqual(updated["history_tokens"], [first["token"], updated["token"]])
        for turn, entry in enumerate([first, updated], 1):
            code, raw, headers = self.request("GET", route + "/D-01/" + entry["token"] + ".json")
            self.assertEqual((code, json.loads(raw)), (200, entry["record"]))
            self.assertIn(f"owner-D-01-turn-{turn}.json", headers["Content-Disposition"])
        self.assertEqual(self.request("GET", route + "/D-08/" + first["token"] + ".json")[0], 404)
        self.assertEqual(self.upload(scope + bound + "&clarification=" + first["token"])[0], 409)
        check(scope + bound + "&clarification=" + updated["token"])
        self.assertEqual(self.clarifications, [updated["record"]])
        code, other = send("D-08", "Частичный ответ.")
        self.assertEqual(code, 200)
        self.assertEqual(self.upload(scope + bound + "&clarification=" + other["token"])[0], 409)
        self.assertEqual(self.upload(scope + bound + "&dialogue_snapshot=1")[0], 409)
        self.assertEqual(self.upload(scope + bound + "&dialogue_snapshot=1&finding=D-01&finding=D-08&clarification=" + updated["token"])[0], 409)
        check(scope + "&finding=D-08")
        self.assertEqual(self.clarifications, [other["record"]])
        path = self.demo / "review.json"
        path.write_bytes(path.read_bytes() + b"\n")
        self.assertEqual(self.request("GET", route)[0], 409)
        self.assertEqual(self.upload(scope + bound + "&clarification=" + updated["token"])[0], 409)
        check(scope)
        self.assertEqual(self.clarifications, [])

    @patch("reviewer.clarify.clarify")
    def test_busy_dialogue_rejects_concurrent_turn_and_recheck_and_recovers_on_failure(self, clarify):
        _, _, send = self.discussion_fixture()
        entered, finish = Event(), Event()
        def unavailable(*args, **kwargs):
            entered.set()
            if not finish.wait(2):
                self.fail("Dialogue did not receive release")
            raise ValueError("private provider diagnostics")
        clarify.side_effect = unavailable
        received = []
        thread = Thread(target=lambda: received.append(send("D-01", "В обработке.")))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(send("D-01", "Дубликат.")[0], 409)
            self.assertEqual(self.upload("/api/runs?mode=recheck&baseline=demo")[0], 409)
        finally:
            finish.set()
            thread.join()
        self.assertEqual(received[0][0], 502)
        self.assertNotIn("private", received[0][1]["error"])
        clarify.side_effect = None
        clarify.return_value = {"state": "needs_followup", "answers": ["Повтор."], "follow_up": "Какой месяц?"}
        self.assertEqual(send("D-01", "Повтор.")[0], 200)

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

    @patch("reviewer.clarify.clarify")
    def test_import_preserves_download_and_restores_owner_discussion_and_recheck(self, clarify):
        raw = (ROOT / "eval/executable/runs/named-state.json").read_bytes() + b"\n "
        headers = {"Origin": self.origin, "Content-Type": "application/json; charset=utf-8"}
        code, body, _ = self.request("POST", "/api/import-report", raw, headers)
        self.assertEqual(code, 201)
        result = json.loads(body)
        identifier, digest = result["id"], sha256(raw).hexdigest()
        self.assertEqual(result, {"id": identifier, "input_name": "statement-status.md", "sha256": digest, "reused": False})
        self.assertEqual(self.status(identifier), {"state": "done", "mode": "full", "progress": [],
                                                  "error": "", "imported": True})
        path = f"/reports/{identifier}/review"
        code, body, response_headers = self.request("GET", path + ".json?download=1")
        self.assertEqual((code, body, response_headers["X-Report-SHA256"]), (200, raw, digest))
        self.assertIn(b"AWAITING_SIGNATURE", self.request("GET", path + ".html")[1])
        self.assertEqual(self.request("GET", f"/reports/{identifier}/executions/{digest}")[0], 200)
        code, body, _ = self.request("POST", "/api/import-report", raw, headers)
        self.assertEqual((code, json.loads(body)), (200, {**result, "reused": True}))
        self.assertEqual(self.calls, [])
        clarify.assert_not_called()
        clarify.return_value = {"state": "needs_followup", "answers": ["Проверка вручную."],
                                "follow_up": "Какой статус вернуть?", "finding_id": "D-01"}
        route = f"/api/clarifications/{identifier}/{digest}"
        payload = {"finding_id": "D-01", "answer": "Проверка вручную.", "previous": None}
        code, reply, _ = self.request("POST", route, json.dumps(payload), headers)
        self.assertEqual(code, 200)
        self.release.set()
        token = json.loads(reply)["token"]
        query = (f"/api/runs?mode=recheck&baseline={identifier}&finding=D-01"
                 f"&baseline_sha256={digest}&clarification={token}")
        code, reply, _ = self.upload(query, name="revised.md")
        self.assertEqual(code, 202)
        self.assertEqual(self.wait_until_done(json.loads(reply)["id"])["state"], "done")
        self.assertEqual(self.calls[0][3].read_bytes(), raw)
        self.assertEqual(self.clarifications, [clarify.return_value])

    def test_import_rejects_malformed_wrong_scope_and_modified_traces(self):
        headers = {"Origin": self.origin, "Content-Type": "application/json"}
        route = "/api/import-report"
        self.assertEqual(self.request("POST", route, b"{}", {})[0], 403)
        self.assertEqual(self.request("POST", route, b"{}", {"Origin": self.origin})[0], 400)
        self.assertEqual(self.request("POST", route, b"{}", {**headers, "Content-Length": str(IMPORT_LIMIT + 1)})[0], 413)
        source = (ROOT / "eval/executable/runs/named-state.json").read_bytes()
        for payload, expected in [(b"[", 400), (b'{"schema_version":NaN}', 400), (b'{"schema_version":1}', 422)]:
            self.assertEqual(self.request("POST", route, payload, headers)[0], expected)
        for change in (lambda r: r["run"].update(input_lines=120_002),
                       lambda r: r["run"].update(page_count=1, pages=[{"page": 1, "start": 1, "end": 1000}]),
                       lambda r: r["whole"]["findings"][0].update(priority="редакционный"),
                       lambda r: r["executables"]["items"][0]["result"]["scenario"]["first_difference"].update(second="READY")):
            report = json.loads(source)
            change(report)
            code, message, _ = self.request("POST", route, json.dumps(report), headers)
            self.assertEqual(code, 422, message)
        self.assertEqual(self.app.runs, {})
        self.assertEqual(self.calls, [])

    def test_failed_worker_does_not_expose_exception_or_reports(self):
        code, body, _ = self.upload(name="failure.md")
        self.assertEqual(code, 202)
        identifier = json.loads(body)["id"]
        status = self.wait_until_done(identifier)
        self.assertEqual(status["state"], "failed")
        self.assertNotIn("secret-token", status["error"])
        self.assertEqual(self.request("GET", f"/reports/{identifier}/review.html")[0], 409)


class ImportedReportTest(unittest.TestCase):
    def test_concurrent_imports_register_one_completed_original_without_a_worker(self):
        raw = (ROOT / "runs/bcc-final/review.json").read_bytes()
        with TemporaryDirectory() as temp:
            storage = Path(temp)
            app = Workbench(storage, worker=lambda *a: self.fail("Import must not launch a review"))
            with ThreadPoolExecutor(max_workers=5) as pool:
                results = list(pool.map(app.import_report, [raw] * 5))
            self.assertEqual(len({r["id"] for r in results}), 1)
            self.assertEqual(sum(not r["reused"] for r in results), 1)
            self.assertEqual(len(list(storage.iterdir())), 1)
            identifier = results[0]["id"]
            self.assertEqual(app.status(identifier)["mode"], "full")
            directory = app.reports(identifier, baseline=True)
            self.assertEqual((directory / "review.json").read_bytes(), raw)
            self.assertTrue((directory / "review.html").is_file())
            self.assertNotEqual(app.import_report(raw + b"\n")["id"], identifier)

    def test_failed_publication_leaves_no_run_and_can_be_retried(self):
        raw = (ROOT / "eval/executable/runs/named-state.json").read_bytes()
        with TemporaryDirectory() as temp:
            storage = Path(temp)
            app = Workbench(storage)
            with patch.object(Path, "rename", side_effect=OSError("disk unavailable")), self.assertRaises(OSError):
                app.import_report(raw)
            self.assertFalse(app.runs)
            self.assertEqual(list(storage.iterdir()), [])
            self.assertFalse(app.import_report(raw)["reused"])


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

    def test_proof_phase_counts_completed_checks_and_explicit_abstentions_once(self):
        run = Run("example", "full", Path("unused"))
        run.record("План расчётов · примеров: 3")
        self.assertEqual(run.phase, "executables")
        run.record("Исполняемый пример D-02 · не подтверждён")
        run.record("Исполняемый пример D-02 · не подтверждён")
        self.assertEqual((len(run.checked_proofs), run.phase), (1, "executables"))
        run.record("Исполняемый пример D-03 · модель недоступна")
        self.assertEqual((len(run.checked_proofs), run.phase), (2, "executables"))
        run.record("Исполняемый пример D-01 · подтверждён")
        self.assertEqual((len(run.checked_proofs), run.proof_total, run.phase), (3, 3, "render"))

    def test_provider_quota_requires_a_cli_diagnostic_and_keeps_a_saved_demo_available(self):
        from reviewer.serve import QUOTA_MESSAGE, SafeWorkerError

        cases = [
            ("Прогон не завершён: Весь документ: Codex CLI завершился с кодом 1. quota_exhausted: private-account", QUOTA_MESSAGE),
            ("Повторная проверка не завершена: Codex CLI завершился с кодом 1. HTTP 429 Too Many Requests", QUOTA_MESSAGE),
            ("Прогон не завершён: Схема ответа: в строке 41 упоминается HTTP 429", "Ошибка"),
            ("ТЗ: проверяется ситуация quota_exhausted и HTTP 429", "Ошибка"),
        ]
        for line, expected in cases:
            with self.subTest(line=line), self.assertRaises(SafeWorkerError) as failure:
                execute([sys.executable, "-c", "import sys; print(sys.argv[1]); sys.exit(1)", line], lambda _: None, "Ошибка")
            self.assertEqual(str(failure.exception), expected)

    def test_only_fixed_cli_output_can_reach_client(self):
        progress = []
        command = [sys.executable, "-c",
                   "print('План проверки · групп строк: 3'); print('План проверки · групп строк: secret'); "
                   "print('Сверка требований · начата'); print('Весь документ · проверено'); print('key=xyz'); "
                   "print('Codex CLI не найден: secret'); raise SystemExit(1)"]
        with self.assertRaisesRegex(RuntimeError, "^Установите Codex CLI перед запуском проверки\\.$"):
            execute(command, progress.append, "Ошибка")
        self.assertEqual(progress, ["План проверки · групп строк: 3", CROSS_CHECK, "Весь документ · проверено"])
