import http.client
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest

from cloud.public import PublicServer


class HostedWorkbenchTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.finish_job = Event()
        try:
            self.server = PublicServer(("127.0.0.1", 0), Path(directory.name),
                                       "https://review.example", worker=self.worker)
        except PermissionError:
            self.skipTest("Local server sockets unavailable")
        thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.finish_job.set)

    def worker(self, mode, document, baseline, json_report, html_report, model, progress):
        if not self.finish_job.wait(5):
            raise TimeoutError()
        json_report.write_text('{"ok":true}')
        html_report.write_text("my report")

    def request(self, method, path, cookie="", origin=None, body=None):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            headers = {"Host": "review.example"}
            if cookie:
                headers["Cookie"] = cookie
            if origin:
                headers.update(Origin=origin, **{"X-File-Name": "draft.md"})
            client.request(method, path, body=body, headers=headers)
            reply = client.getresponse()
            return reply.status, reply.read(), dict(reply.getheaders())
        finally:
            client.close()

    def new_visitor(self):
        code, _, headers = self.request("GET", "/")
        self.assertEqual(code, 200)
        cookie = headers["Set-Cookie"]
        for flag in ("Secure", "HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(flag, cookie)
        return cookie.split(";", 1)[0]

    def upload(self, cookie, origin="https://review.example"):
        return self.request("POST", "/api/runs?mode=full", cookie, origin, b"my requirements")

    def test_health_and_visitor_isolation_including_completed_download(self):
        self.assertEqual(self.request("GET", "/healthz")[:2], (200, b"ok"))
        self.assertFalse(self.server.visits)
        first, second = self.new_visitor(), self.new_visitor()
        self.assertNotEqual(first, second)
        self.assertEqual(self.upload(first, "https://other.example")[0], 403)
        status, raw, _ = self.upload(first)
        self.assertEqual(status, 202)
        job = json.loads(raw)["id"]
        self.assertEqual(self.request("GET", "/api/status/" + job, second)[0], 404)
        self.assertEqual(self.request("GET", "/api/status/" + job)[0], 401)
        self.finish_job.set()
        for entry in self.server.visits.values():
            entry.app.wait()
        path = "/reports/" + job + "/review.html"
        self.assertEqual(self.request("GET", path, first)[:2], (200, b"my report"))
        self.assertEqual(self.request("GET", path, second)[0], 404)
        code, pdf, _ = self.request("GET", "/reports/demo/review.pdf")
        self.assertEqual(code, 200)
        self.assertTrue(pdf.startswith(b"%PDF-"))

    def test_model_concurrency_across_visitors(self):
        cookies = [self.new_visitor() for _ in range(3)]
        self.assertEqual([self.upload(c)[0] for c in cookies], [202, 202, 429])
        self.assertEqual(len(self.server.model_requests), 2)
        self.finish_job.set()
        for entry in self.server.visits.values():
            entry.app.wait()
        self.assertEqual(self.upload(cookies[2])[0], 202)
