"""Run the local review workbench and execute review commands in isolated directories."""

import argparse
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Lock, Thread
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4


ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "reviewer" / "workbench.html"
REPORT_FILES = {"review.html": "text/html; charset=utf-8", "review.json": "application/json; charset=utf-8"}
GALLERY_FILES = {
    "/design/concepts/index.html": "text/html; charset=utf-8",
    "/design/concepts/gallery.css": "text/css; charset=utf-8",
    "/design/concepts/case.js": "text/javascript; charset=utf-8",
    "/design/concepts/gallery.js": "text/javascript; charset=utf-8",
    **{f"/runs/bcc-final/{name}": kind for name, kind in REPORT_FILES.items()},
}
LIMITS = {".md": 120_000, ".txt": 120_000, ".pdf": 16_000_000}
JOB_ID = r"[0-9a-f]{32}"
PROGRESS = re.compile(r"(?:Сценарии|Блоки [0-9]+–[0-9]+|Весь документ|Повторная проверка) · "
                      r"(?:проверено|проверена сохранённая стадия)")
SAFE_FAILURES = {
    "Документ пуст": "Документ пуст.",
    "Новая версия документа пуста": "Новая версия документа пуста.",
    "нет текстового слоя": "В PDF отсутствует текстовый слой. Загрузите версию после OCR.",
    "PDF повреждён или требует пароль": "PDF повреждён или защищён паролем.",
    "установите зависимости": "Для обработки PDF установите зависимости из requirements.txt.",
    "Codex CLI не найден": "Установите Codex CLI перед запуском проверки.",
    "Codex CLI не завершил запрос за": "Codex CLI не ответил за отведённое время.",
    "UnicodeDecodeError": "Сохраните текстовый файл в UTF-8.",
    "can't decode byte": "Сохраните текстовый файл в UTF-8.",
}


class WorkbenchError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class SafeWorkerError(RuntimeError):
    """A fixed message safe to return in the workbench."""


@dataclass
class Run:
    id: str
    mode: str
    directory: Path
    state: str = "queued"
    progress: list = field(default_factory=list)
    error: str = ""


def demo_directory(root):
    for name in ("bcc-benchmark", "bcc-final"):
        directory = root / "runs" / name
        if all((directory / filename).is_file() for filename in REPORT_FILES):
            return directory
    return None


def upload_name(header):
    if not header or len(header) > 768 or re.search(r"%(?![0-9a-fA-F]{2})", header):
        raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Укажите имя файла в X-File-Name.")
    try:
        name = unquote(header, errors="strict")
    except UnicodeError as exc:
        raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Имя файла должно быть закодировано в UTF-8.") from exc
    if (len(name.encode("utf-8")) > 240 or name.startswith(".") or name != name.strip()
            or any(char in "/\\:" or not char.isprintable() for char in name)):
        raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Недопустимое имя файла.")
    if Path(name).suffix.lower() not in LIMITS:
        raise WorkbenchError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Поддерживаются файлы .md, .txt и .pdf.")
    return name


def execute(command, progress, failure):
    """Display only fixed CLI progress markers and a curated set of failure messages."""
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    with subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          start_new_session=True) as process:
        known_failure = []

        def read_output():
            while chunk := process.stdout.readline(4096):
                line = chunk.decode("utf-8", errors="replace").strip()
                if PROGRESS.fullmatch(line):
                    progress(line)
                elif not known_failure:
                    for fragment, message in SAFE_FAILURES.items():
                        if fragment in line:
                            known_failure.append(message)
                            break

        reader = Thread(target=read_output, daemon=True)
        reader.start()
        try:
            code = process.wait(timeout=1800)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise SafeWorkerError("Истекло время выполнения проверки.") from exc
        finally:
            reader.join(timeout=5)
        if code:
            raise SafeWorkerError(known_failure[0] if known_failure else failure)


def cli_worker(mode, document, baseline, json_report, html_report, model, progress):
    python = [sys.executable, "-m"]
    if mode == "full":
        command = [*python, "reviewer", str(document), str(json_report)]
    else:
        command = [*python, "reviewer.recheck", str(baseline), str(document), str(json_report)]
    if model:
        command.extend(["--model", model])
    execute(command, progress, "CLI завершил проверку с ошибкой. Проверьте доступность Codex и параметры модели.")
    progress("Отчёт JSON сохранён")
    execute([*python, "reviewer.render", str(json_report), str(html_report)], progress,
            "Не удалось создать HTML-отчёт.")
    progress("Отчёт HTML сохранён")


class Workbench:
    def __init__(self, storage, root=ROOT, worker=cli_worker, model=None):
        self.storage = storage
        self.root = root
        self.worker = worker
        self.model = model
        self.runs = {}
        self.lock = Lock()
        self.upload_lock = Lock()
        self.active = None
        self.thread = None

    def status(self, identifier):
        with self.lock:
            run = self.runs.get(identifier)
            if run is None:
                raise WorkbenchError(HTTPStatus.NOT_FOUND, "Прогон не найден.")
            return {"state": run.state, "progress": list(run.progress), "error": run.error, "mode": run.mode}

    def reports(self, identifier, baseline=False):
        if identifier == "demo":
            directory = demo_directory(self.root)
            if not directory:
                raise WorkbenchError(HTTPStatus.NOT_FOUND, "Демо-отчёт не найден.")
            return directory
        with self.lock:
            run = self.runs.get(identifier)
            if run is None:
                raise WorkbenchError(HTTPStatus.NOT_FOUND, "Прогон не найден.")
            if run.state != "done" or (baseline and run.mode != "full"):
                raise WorkbenchError(HTTPStatus.CONFLICT, "Требуется завершённый полный отчёт." if baseline
                                     else "Отчёт ещё недоступен.")
            return run.directory

    def start(self, mode, baseline, name, length, stream):
        if not self.upload_lock.acquire(blocking=False):
            raise WorkbenchError(HTTPStatus.CONFLICT, "Уже загружается другой документ.")
        try:
            with self.lock:
                if self.active is not None:
                    raise WorkbenchError(HTTPStatus.CONFLICT, "Дождитесь завершения текущей проверки.")
            baseline_report = self.reports(baseline, baseline=True) / "review.json" if baseline else None
            identifier = uuid4().hex
            directory = self.storage / identifier
            payload = stream.read(length)
            if len(payload) != length:
                raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Получен неполный файл.")
            uploads = directory / "upload"
            uploads.mkdir(parents=True, mode=0o700)
            document = uploads / name
            document.write_bytes(payload)
            run = Run(identifier, mode, directory)
            with self.lock:
                self.runs[identifier] = run
                self.active = identifier
                self.thread = Thread(target=self._run, args=(run, document, baseline_report), daemon=True)
                self.thread.start()
            return identifier
        finally:
            self.upload_lock.release()

    def _run(self, run, document, baseline):
        def on_progress(line):
            with self.lock:
                run.progress.append(line)
                del run.progress[:-100]

        json_report = run.directory / "review.json"
        html_report = run.directory / "review.html"
        with self.lock:
            run.state = "running"
        state, error = "done", ""
        try:
            self.worker(run.mode, document, baseline, json_report, html_report, self.model, on_progress)
            if not all((run.directory / name).is_file() for name in REPORT_FILES):
                raise SafeWorkerError("Проверка не сформировала оба отчёта.")
        except SafeWorkerError as exc:
            state, error = "failed", str(exc)
        except Exception:
            state, error = "failed", "Не удалось завершить проверку."
        finally:
            with self.lock:
                run.state, run.error = state, error
                self.active = None

    def wait(self):
        with self.lock:
            thread = self.thread
        if thread:
            thread.join()


class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version = "ReviewerWorkbench"

    def setup(self):
        self.request.settimeout(20)
        super().setup()

    def log_message(self, format, *args):
        pass

    def route(self, *, write=False):
        hosts = self.headers.get_all("Host", [])
        port = self.server.server_port
        allowed = {f"{host}:{port}" for host in ("127.0.0.1", "localhost")}
        if port == 80:
            allowed.update(("127.0.0.1", "localhost"))
        if len(hosts) != 1 or hosts[0].lower() not in allowed:
            raise WorkbenchError(HTTPStatus.FORBIDDEN, "Доступ разрешён через локальный адрес.")
        if write and self.headers.get_all("Origin", []) != ["http://" + hosts[0].lower()]:
            raise WorkbenchError(HTTPStatus.FORBIDDEN, "Загрузка разрешена со страницы локального приложения.")
        if not self.path.startswith("/") or self.path.startswith("//"):
            raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Неверный путь запроса.")
        return urlsplit(self.path)

    def reply(self, status, body, content_type, download=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                         "frame-ancestors 'self'; base-uri 'none'; form-action 'self'; object-src 'none'")
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(body)

    def json(self, status, response):
        self.reply(status, json.dumps(response, ensure_ascii=False).encode(), REPORT_FILES["review.json"])

    def do_GET(self):
        try:
            route = self.route()
            app = self.server.app
            if route.path in GALLERY_FILES:
                artifact = app.root / route.path[1:]
                self.reply(HTTPStatus.OK, artifact.read_bytes(), GALLERY_FILES[route.path])
                return
            if route.path == "/":
                self.reply(HTTPStatus.OK, (app.root / "reviewer" / PAGE.name).read_bytes(),
                           REPORT_FILES["review.html"])
                return
            status = re.fullmatch(rf"/api/status/({JOB_ID})", route.path)
            if status:
                self.json(HTTPStatus.OK, app.status(status[1]))
                return
            report = re.fullmatch(rf"/reports/(demo|{JOB_ID})/(review\.(?:html|json))", route.path)
            if report:
                name = report[2]
                directory = app.reports(report[1])
                download = name if parse_qs(route.query).get("download") == ["1"] else None
                self.reply(HTTPStatus.OK, (directory / name).read_bytes(), REPORT_FILES[name], download)
                return
            raise WorkbenchError(HTTPStatus.NOT_FOUND, "Страница не найдена.")
        except WorkbenchError as exc:
            self.json(exc.code, {"error": str(exc)})
        except FileNotFoundError:
            self.json(HTTPStatus.NOT_FOUND, {"error": "Файл не найден."})
        except OSError:
            self.json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Не удалось прочитать файл."})

    def do_POST(self):
        try:
            route = self.route(write=True)
            if route.path != "/api/runs":
                raise WorkbenchError(HTTPStatus.NOT_FOUND, "Страница не найдена.")
            query = parse_qs(route.query, keep_blank_values=True)
            modes = query.get("mode", [])
            baselines = query.get("baseline", [])
            if modes == ["full"] and not baselines and set(query) == {"mode"}:
                baseline = None
            elif (modes == ["recheck"] and len(baselines) == 1 and set(query) == {"mode", "baseline"}
                  and re.fullmatch(rf"demo|{JOB_ID}", baselines[0])):
                baseline = baselines[0]
            else:
                raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Укажите mode=full или mode=recheck с baseline.")
            names = self.headers.get_all("X-File-Name", [])
            if len(names) != 1:
                raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Укажите одно имя файла в X-File-Name.")
            name = upload_name(names[0])
            sizes = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(sizes) != 1 or not re.fullmatch(r"[0-9]{1,10}", sizes[0]):
                raise WorkbenchError(HTTPStatus.LENGTH_REQUIRED, "Передайте файл с известным размером.")
            size = int(sizes[0])
            if size <= 0:
                raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Загрузите непустой файл.")
            if size > LIMITS[Path(name).suffix.lower()]:
                raise WorkbenchError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Файл превышает допустимый размер.")
            identifier = self.server.app.start(modes[0], baseline, name, size, self.rfile)
            self.json(HTTPStatus.ACCEPTED, {"id": identifier})
        except WorkbenchError as exc:
            self.json(exc.code, {"error": str(exc)})
        except TimeoutError:
            self.json(HTTPStatus.REQUEST_TIMEOUT, {"error": "Истекло время загрузки файла."})
        except OSError:
            self.json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Не удалось сохранить загруженный файл."})


class WorkbenchServer(ThreadingHTTPServer):
    def __init__(self, port, app):
        self.app = app
        super().__init__(("127.0.0.1", port), WorkbenchHandler)


def main():
    parser = argparse.ArgumentParser(description="Запустить локальное рабочее место ревью")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--model", help="Модель для новых прогонов (по умолчанию REVIEW_MODEL или модель CLI)")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("Порт должен быть от 0 до 65535.")
    with TemporaryDirectory(prefix="reviewer-workbench-") as directory:
        app = Workbench(Path(directory), model=args.model)
        try:
            with WorkbenchServer(args.port, app) as server:
                print(f"Рабочее место открыто на http://127.0.0.1:{server.server_port}", flush=True)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
        except OSError as exc:
            parser.exit(1, f"Не удалось запустить рабочее место: {exc.strerror or 'ошибка сокета'}\n")
        finally:
            app.wait()


if __name__ == "__main__":
    main()
