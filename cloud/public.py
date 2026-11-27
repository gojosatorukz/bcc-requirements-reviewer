"""Single-instance jury workbench behind a TLS-terminating hosting proxy."""

from collections import deque
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import ThreadingHTTPServer
import os
from pathlib import Path
import re
import secrets
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore, Lock
from time import monotonic
from urllib.parse import urlsplit

from reviewer.serve import SAVED_EXAMPLES, Workbench, WorkbenchError, WorkbenchHandler, cli_worker


COOKIE = "__Host-razbor"
IDLE_SECONDS = 2 * 3600


@dataclass
class Visit:
    directory: TemporaryDirectory
    app: Workbench
    touched: float
    requests: int = 0


class PublicServer(ThreadingHTTPServer):
    daemon_threads = False

    def __init__(self, address, storage, origin, worker=cli_worker):
        target = urlsplit(origin)
        if target.scheme != "https" or not re.fullmatch(r"[a-z0-9.-]+(?::[0-9]+)?", target.netloc) or target.path or target.query or target.fragment:
            raise ValueError("Укажите публичный HTTPS origin без пути, например https://review.example.kz")
        self.origin = origin
        self.host = target.netloc
        self.storage = storage
        self.worker = worker
        self.lock = Lock()
        self.visits = {}
        self.model_slots = BoundedSemaphore(2)
        self.model_requests = deque()
        self.last_sweep = monotonic()
        super().__init__(address, PublicHandler)

    def visit(self, token, landing):
        with self.lock:
            entry = self.visits.get(token)
            if not entry:
                if not landing:
                    raise WorkbenchError(HTTPStatus.UNAUTHORIZED, "Откройте главную страницу, чтобы начать новую сессию.")
                if len(self.visits) >= 128:
                    raise WorkbenchError(HTTPStatus.SERVICE_UNAVAILABLE, "Все сессии заняты. Попробуйте позже.")
                token = secrets.token_urlsafe(32)
                directory = TemporaryDirectory(dir=self.storage)
                app = Workbench(Path(directory.name), worker=self.worker, review_slots=self.model_slots,
                                admit_model_request=self.admit_model_request, max_reports=12)
                entry = Visit(directory, app, monotonic())
                self.visits[token] = entry
            entry.touched = monotonic()
            entry.requests += 1
            return token, entry

    def admit_model_request(self):
        now = monotonic()
        with self.lock:
            while self.model_requests and self.model_requests[0] <= now - 3600:
                self.model_requests.popleft()
            if len(self.model_requests) >= 40:
                raise WorkbenchError(HTTPStatus.TOO_MANY_REQUESTS, "Часовой лимит запусков и вопросов этого сайта исчерпан.")
            self.model_requests.append(now)

    def service_actions(self):
        now = monotonic()
        if now - self.last_sweep < 60:
            return
        self.last_sweep = now
        with self.lock:
            expired = []
            for token, entry in self.visits.items():
                if not entry.requests and now - entry.touched >= IDLE_SECONDS:
                    with entry.app.lock:
                        if entry.app.active is None:
                            expired.append(token)
            stale = [self.visits.pop(token) for token in expired]
        for entry in stale:
            entry.directory.cleanup()

    def server_close(self):
        super().server_close()
        for entry in self.visits.values():
            entry.app.wait()
            entry.directory.cleanup()
        self.visits.clear()


class PublicHandler(WorkbenchHandler):
    allow_project_runs = False
    entry = None
    new_cookie = None

    @property
    def app(self):
        return self.entry.app

    def route(self, *, write=False):
        if self.headers.get_all("Host", []) != [self.server.host]:
            raise WorkbenchError(HTTPStatus.FORBIDDEN, "Неверный адрес сайта.")
        if write and self.headers.get_all("Origin", []) != [self.server.origin]:
            raise WorkbenchError(HTTPStatus.FORBIDDEN, "Отправьте документ из этой вкладки приложения.")
        path = urlsplit(self.path)
        if not self.path.startswith("/") or self.path.startswith("//") or path.netloc:
            raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Неверный путь запроса.")
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except CookieError:
            jar = SimpleCookie()
        token = jar[COOKIE].value if COOKIE in jar else None
        landing = path.path in SAVED_EXAMPLES or path.path in (
            "/", "/reports/demo/review.html", "/reports/demo/review.pdf", "/reports/demo/review.json")
        key, self.entry = self.server.visit(token, landing=not write and landing)
        if key != token:
            self.new_cookie = key
        return path

    def do_GET(self):
        if self.path == "/healthz":
            self.reply(HTTPStatus.OK, b"ok", "text/plain")
            return
        super().do_GET()

    def do_POST(self):
        # Assistant and owner calls run in the request thread; review jobs release
        # their reserved slot when their background worker finishes.
        if self.path.startswith(("/api/assistant/", "/api/clarifications/")):
            if not self.server.model_slots.acquire(blocking=False):
                self.json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "Агент занят. Попробуйте позже."})
                return
            try:
                super().do_POST()
            finally:
                self.server.model_slots.release()
        else:
            super().do_POST()

    def end_headers(self):
        if self.new_cookie:
            self.send_header("Set-Cookie", f"{COOKIE}={self.new_cookie}; Path=/; Secure; HttpOnly; SameSite=Strict")
        super().end_headers()

    def finish(self):
        try:
            super().finish()
        finally:
            if self.entry:
                with self.server.lock:
                    self.entry.requests -= 1
                    self.entry.touched = monotonic()


def main():
    origin = os.environ.get("PUBLIC_ORIGIN")
    if not origin and os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
        origin = "https://" + os.environ["RENDER_EXTERNAL_HOSTNAME"]
    if not origin:
        raise SystemExit("Укажите PUBLIC_ORIGIN или RENDER_EXTERNAL_HOSTNAME.")
    with TemporaryDirectory(prefix="reviewer-hosted-") as storage:
        with PublicServer(("0.0.0.0", int(os.environ.get("PORT", "10000"))), storage, origin) as server:
            server.serve_forever()


if __name__ == "__main__":
    main()
