"""Run the local review workbench and execute review commands in isolated directories."""

import argparse
from dataclasses import dataclass, field
from hashlib import sha256
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


from .input import INPUT_LIMITS


ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "reviewer" / "workbench.html"
REPORT_FILES = {"review.html": "text/html; charset=utf-8", "review.json": "application/json; charset=utf-8"}
STATIC_FILES = {
    "/reviewer/guided.css": "text/css; charset=utf-8",
    "/reviewer/guided.js": "text/javascript; charset=utf-8",
    "/reviewer/execution.css": "text/css; charset=utf-8",
    "/reviewer/explorer.js": "text/javascript; charset=utf-8",
    "/design/concepts/index.html": "text/html; charset=utf-8",
    "/design/concepts/gallery.css": "text/css; charset=utf-8",
    "/design/concepts/case.js": "text/javascript; charset=utf-8",
    "/design/concepts/gallery.js": "text/javascript; charset=utf-8",
    **{f"/runs/bcc-final/{name}": kind for name, kind in REPORT_FILES.items()},
}
JOB_ID = r"[0-9a-f]{32}"
FINDING_ID = r"D-(?:0[1-9]|[1-9][0-9]+)"
IMPORT_LIMIT = 8 * 1024 * 1024
MAX_INPUT_LINES = INPUT_LIMITS[".txt"] + 1
DIALOGUE_ROUTE = rf"/api/clarifications/(demo|{JOB_ID})/([0-9a-f]{{64}})"
PROGRESS = re.compile(r"(?:Сценарии|Блоки [0-9]+–[0-9]+|Весь документ|Повторная проверка) · "
                      r"(?:проверено|проверена сохранённая стадия)")
PROOF_PLAN = re.compile(r"План расчётов · примеров: ([0-9]{1,6})")
PROOF_DONE = re.compile(rf"Исполняемый пример ({FINDING_ID}) · (?:подтверждён|не подтверждён|модель недоступна)")
PLAN = re.compile(r"План проверки · групп строк: ([0-9]{1,6})")
CROSS_CHECK = "Сверка требований · начата"
SAFE_FAILURES = {
    "DOCX содержит": "В Word-файле есть элементы, которые требуют экспорта в PDF с текстовым слоем.",
    "DOCX повреждён": "Word-файл повреждён или защищён паролем.",
    "DOCX не содержит": "В Word-файле нет доступного основного текста.",
    "Документ пуст": "Документ пуст.",
    "Новая версия документа пуста": "Новая версия документа пуста.",
    "нет текстового слоя": "В PDF отсутствует текстовый слой. Загрузите версию после OCR.",
    "PDF повреждён или требует пароль": "PDF повреждён или защищён паролем.",
    "установите зависимости": "Для обработки PDF установите зависимости из requirements.txt.",
    "401 Unauthorized": "Для нового ответа войдите в Codex командой codex login и проверьте codex login status.",
    "not logged in": "Для нового ответа войдите в Codex командой codex login и проверьте codex login status.",
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


def imported_full_report(raw):
    from .full_render import render_full

    if len(raw) > IMPORT_LIMIT:
        raise WorkbenchError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Размер отчёта превышает 8 МиБ.")

    def invalid_number(token):
        raise ValueError(f"{token} не является числом JSON")

    try:
        report = json.loads(raw.decode("utf-8-sig"), parse_constant=invalid_number)
    except (ValueError, RecursionError) as exc:
        raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Не удалось прочитать файл как JSON в UTF-8.") from exc
    if (not isinstance(report, dict) or "report_type" in report or
            type(report.get("schema_version")) is not int or report["schema_version"] != 2):
        raise WorkbenchError(HTTPStatus.UNPROCESSABLE_ENTITY, "Нужен JSON полного ревью требований версии 2.")
    try:
        run = report["run"]
        name, count = run["input_name"], run["input_lines"]
        if not isinstance(name, str) or not 1 <= len(name) <= 1024 or not name.strip() or not name.isprintable():
            raise ValueError("Некорректное имя исходного документа")
        if type(count) is not int or not 1 <= count <= MAX_INPUT_LINES:
            raise WorkbenchError(HTTPStatus.UNPROCESSABLE_ENTITY,
                                 f"Число строк исходного документа должно быть от 1 до {MAX_INPUT_LINES}.")
        if not re.fullmatch(r"[a-f0-9]{64}", run["input_sha256"]):
            raise ValueError("Некорректный хеш документа")
        if not isinstance(report["source"], list) or not report["source"]:
            raise ValueError("Нет текста исходного документа")
        # PDF ranges are expanded by the renderer. Validate them before allocating line maps.
        previous_page = previous_end = 0
        pages = run.get("pages", [])
        if not isinstance(pages, list) or ("page_count" in run and
                (type(run["page_count"]) is not int or run["page_count"] < 1)):
            raise ValueError("Некорректные страницы")
        for page in pages:
            number, start, end = page["page"], page["start"], page["end"]
            if (any(type(n) is not int for n in (number, start, end)) or
                    not previous_page < number <= run["page_count"] or not previous_end < start <= end <= count):
                raise ValueError("Некорректные диапазоны страниц")
            previous_page, previous_end = number, end
        html = render_full(report).encode("utf-8")
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        raise WorkbenchError(HTTPStatus.UNPROCESSABLE_ENTITY,
                             "Отчёт не прошёл проверку структуры, ссылок на ТЗ или сохранённых вычислений.") from exc
    return name, html


@dataclass
class Run:
    id: str
    mode: str
    directory: Path
    state: str = "queued"
    progress: list = field(default_factory=list)
    error: str = ""
    group_total: int | None = None
    checked_groups: set = field(default_factory=set)
    phase: str = "groups"
    proof_total: int | None = None
    checked_proofs: set = field(default_factory=set)
    imported: bool = False

    def record(self, line):
        plan = PLAN.fullmatch(line)
        if plan:
            self.group_total = int(plan[1])
            return
        proof_plan = PROOF_PLAN.fullmatch(line)
        if proof_plan:
            self.proof_total = int(proof_plan[1])
            self.phase = "executables" if self.proof_total else "render"
            return
        proof = PROOF_DONE.fullmatch(line)
        if proof:
            self.checked_proofs.add(proof[1])
            if len(self.checked_proofs) == self.proof_total:
                self.phase = "render"
        if line == CROSS_CHECK:
            self.phase = "whole"
            return
        stage = line.split(" · ", 1)[0]
        if stage.startswith("Блоки "):
            self.checked_groups.add(stage)
        elif stage == "Весь документ":
            self.phase = "render"
        self.progress.append(line)
        del self.progress[:-100]


def demo_directory(root):
    for name in ("bcc-executable", "bcc-submission", "bcc-priority", "bcc-benchmark", "bcc-final"):
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
    if Path(name).suffix.lower() not in INPUT_LIMITS:
        raise WorkbenchError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Поддерживаются файлы .md, .txt, .pdf и .docx.")
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
                if (PROGRESS.fullmatch(line) or PLAN.fullmatch(line) or line == CROSS_CHECK
                        or PROOF_PLAN.fullmatch(line) or PROOF_DONE.fullmatch(line)):
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


def cli_worker(mode, document, baseline, json_report, html_report, model, progress, findings=(),
               decisions=(), baseline_sha256=None, clarifications=()):
    python = [sys.executable, "-m"]
    if mode == "full":
        command = [*python, "reviewer", str(document), str(json_report)]
    else:
        command = [*python, "reviewer.recheck", str(baseline), str(document), str(json_report)]
        for finding in findings:
            command.extend(["--finding", finding])
        for decision in decisions:
            command.extend(["--decision", decision])
        for record_path in clarifications:
            command.extend(["--clarification", str(record_path)])
        if decisions or clarifications:
            command.extend(["--baseline-sha256", baseline_sha256])
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
        self.import_lock = Lock()
        self.imported = {}
        self.active = None
        self.thread = None
        self.dialogues = {}
        self.pending_dialogues = set()

    def import_report(self, raw):
        digest = sha256(raw).hexdigest()
        # Only imports share this lock. Active reviews, downloads and owner answers continue.
        with self.import_lock:
            if digest in self.imported:
                return {**self.imported[digest], "reused": True}
            name, html = imported_full_report(raw)
            identifier = uuid4().hex
            directory = self.storage / identifier
            self.storage.mkdir(mode=0o700, parents=True, exist_ok=True)
            with TemporaryDirectory(prefix=".import-", dir=self.storage) as temp:
                stage = Path(temp) / "report"
                stage.mkdir(mode=0o700)
                (stage / "review.json").write_bytes(raw)
                (stage / "review.html").write_bytes(html)
                stage.rename(directory)
            run = Run(identifier, "full", directory, state="done", phase="render", imported=True)
            with self.lock:
                self.runs[identifier] = run
            result = {"id": identifier, "input_name": name, "sha256": digest}
            self.imported[digest] = result
            return {**result, "reused": False}

    def dialogue_report(self, baseline, sha):
        path = self.reports(baseline, baseline=True) / "review.json"
        if sha256(path.read_bytes()).hexdigest() != sha:
            raise WorkbenchError(HTTPStatus.CONFLICT, "Базовый отчёт изменился. Обновите страницу.")
        return path

    def conversations(self, baseline, sha):
        self.dialogue_report(baseline, sha)
        with self.lock:
            return [item for key, item in self.dialogues.items() if key[:2] == (baseline, sha)]

    def saved_example(self, baseline, sha):
        if baseline != "demo":
            return None
        folder = self.reports(baseline) / "owner"
        try:
            record = json.loads((folder / "dialogue.json").read_bytes())
        except (FileNotFoundError, ValueError):
            return None
        if record.get("baseline_sha256") != sha:
            return None
        return {"finding_id": record["finding_id"], "dialogue": "/reports/demo/owner/dialogue.html",
                "recheck": "/reports/demo/owner/wrong-revision.html"}

    def execution_panels(self, baseline, sha):
        from .execution_view import render_executions
        from .full_render import validate_report

        raw = (self.reports(baseline, baseline=True) / "review.json").read_bytes()
        if sha256(raw).hexdigest() != sha:
            raise WorkbenchError(HTTPStatus.CONFLICT, "Версия отчёта изменилась. Обновите страницу.")
        try:
            report = json.loads(raw)
            lines = validate_report(report)
            items = render_executions(report, lines)
        except (ValueError, KeyError, TypeError) as exc:
            raise WorkbenchError(HTTPStatus.UNPROCESSABLE_ENTITY, "Расчёт из отчёта не прошёл проверку.") from exc
        return {"sha256": sha, "items": items}

    def answer(self, baseline, sha, payload):
        from .clarify import MAX_ANSWER, MAX_TURNS, clarify, next_question

        if (not isinstance(payload, dict) or set(payload) != {"finding_id", "answer", "previous"} or
                not isinstance(payload["finding_id"], str) or not re.fullmatch(FINDING_ID, payload["finding_id"]) or
                not isinstance(payload["answer"], str) or not payload["answer"].strip() or len(payload["answer"]) > MAX_ANSWER or
                (payload["previous"] is not None and (not isinstance(payload["previous"], str) or
                 not re.fullmatch(JOB_ID, payload["previous"])))):
            raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Передайте номер замечания и ответ длиной от 1 до 2400 символов.")
        path = self.dialogue_report(baseline, sha)
        fid = payload["finding_id"]
        self.check_findings(path, [fid])
        key = (baseline, sha, fid)
        with self.lock:
            previous = self.dialogues.get(key, {})
            if payload["previous"] != previous.get("token") or key in self.pending_dialogues:
                raise WorkbenchError(HTTPStatus.CONFLICT, "Обсуждение обновилось или ещё обрабатывается. Загрузите актуальную историю.")
            prior = previous.get("record")
            answers = prior["answers"] if prior else []
            if len(answers) >= MAX_TURNS:
                raise WorkbenchError(HTTPStatus.BAD_REQUEST, "В обсуждении уже 12 ответов.")
            self.pending_dialogues.add(key)
        try:
            model = self.model or os.environ.get("REVIEW_MODEL", "gpt-6.2-astra")
            record = clarify(path, fid, [*answers, payload["answer"]], model, sha, prior=prior)
            self.dialogue_report(baseline, sha)
            token = uuid4().hex
            entry = {"token": token, "history_tokens": [*previous.get("history_tokens", []), token],
                     "record": record, "question": next_question(record)}
            dest = self.storage / "clarifications"
            dest.mkdir(mode=0o700, parents=True, exist_ok=True)
            (dest / (entry["token"] + ".json")).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            with self.lock:
                self.dialogues[key] = entry
            return entry
        except (ValueError, KeyError, TypeError) as exc:
            message = next((hint for key, hint in SAFE_FAILURES.items() if key in str(exc)),
                           "Агенту не удалось проверить ответ. Текст можно отправить ещё раз.")
            if "Codex CLI не найден" in str(exc):
                message += " После установки выполните codex login. Черновик ответа сохранён в этой вкладке."
            raise WorkbenchError(HTTPStatus.BAD_GATEWAY, message) from exc
        finally:
            with self.lock:
                self.pending_dialogues.discard(key)

    def status(self, identifier):
        with self.lock:
            run = self.runs.get(identifier)
            if run is None:
                raise WorkbenchError(HTTPStatus.NOT_FOUND, "Прогон не найден.")
            status = {"state": run.state, "progress": list(run.progress), "error": run.error, "mode": run.mode}
            if run.imported:
                status["imported"] = True
            if run.group_total is not None:
                status["coverage"] = {"checked": len(run.checked_groups), "total": run.group_total, "phase": run.phase}
                if run.proof_total is not None:
                    status["coverage"].update(proofs_checked=len(run.checked_proofs), proofs_total=run.proof_total)
            return status

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

    def start(self, mode, baseline, name, length, stream, findings=(), decisions=(), baseline_sha256=None,
              dialogue_tokens=(), dialogue_snapshot=False):
        if not self.upload_lock.acquire(blocking=False):
            raise WorkbenchError(HTTPStatus.CONFLICT, "Уже загружается другой документ.")
        try:
            with self.lock:
                if self.active is not None:
                    raise WorkbenchError(HTTPStatus.CONFLICT, "Дождитесь завершения текущей проверки.")
            baseline_report = self.reports(baseline, baseline=True) / "review.json" if baseline else None
            with self.lock:
                has_dialogue = any(key[0] == baseline for key in (*self.dialogues, *self.pending_dialogues))
            clarifications = []
            if findings or decisions or dialogue_tokens or dialogue_snapshot or has_dialogue:
                eligible, digest = self.check_findings(baseline_report, findings, decisions, baseline_sha256)
                scope = set(findings or [fid for fid, item in eligible.items() if item.get("priority") == "критический"])
                with self.lock:
                    keys = [(baseline, digest, fid) for fid in sorted(scope)]
                    if any(key in self.pending_dialogues for key in keys):
                        raise WorkbenchError(HTTPStatus.CONFLICT, "Дождитесь ответа агента в обсуждении замечания.")
                    current = {key[2]: self.dialogues[key]["token"] for key in keys if key in self.dialogues}
                valid_tokens = set(current.values())
                if (not set(dialogue_tokens) <= valid_tokens or
                        (dialogue_snapshot and set(dialogue_tokens) != valid_tokens)):
                    raise WorkbenchError(HTTPStatus.CONFLICT, "Обсуждение изменилось. Обновите страницу перед проверкой.")
                if any(value.split("=")[0] in current for value in decisions):
                    raise WorkbenchError(HTTPStatus.CONFLICT, "У замечания уже есть ответ своими словами. Обновите страницу.")
                clarifications = [self.storage / "clarifications" / (token + ".json") for token in current.values()]
                if clarifications:
                    baseline_sha256 = digest
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
                self.thread = Thread(target=self._run,
                                     args=(run, document, baseline_report, findings, decisions, baseline_sha256, clarifications),
                                     daemon=True)
                self.thread.start()
            return identifier
        finally:
            self.upload_lock.release()

    @staticmethod
    def check_findings(baseline_report, findings, decisions=(), baseline_sha256=None):
        try:
            raw = baseline_report.read_bytes()
            digest = sha256(raw).hexdigest()
            if baseline_sha256 is not None and digest != baseline_sha256:
                raise WorkbenchError(HTTPStatus.CONFLICT, "Базовый отчёт изменился. Обновите страницу и зафиксируйте решение заново.")
            report = json.loads(raw)
            if report["schema_version"] != 2 or type(report["whole"]["findings"]) is not list:
                raise ValueError("Invalid baseline")
            eligible = {item["id"]: item for item in report["whole"]["findings"]}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise WorkbenchError(HTTPStatus.CONFLICT, "Не удалось прочитать замечания базового отчёта.") from exc
        if any(finding not in eligible for finding in findings):
            raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Выбрано замечание, которого нет в базовом отчёте.")
        for decision in decisions:
            identifier, side = decision.split("=")
            item = eligible.get(identifier, {})
            in_scope = identifier in findings if findings else item.get("priority") == "критический"
            triage = item.get("triage")
            if triage is not None:
                witnesses = triage.get("witness", []) if isinstance(triage, dict) else []
            else:
                candidates = item.get("candidates") or []
                conflicts = report.get("conflicts") or []
                witnesses = [conflicts[n-1] for n in candidates if type(n) is int and 1 <= n <= len(conflicts)]
                if len(candidates) != len(witnesses):
                    witnesses = []
            if (not in_scope or item.get("priority") != "критический" or item.get("category") != "нарушение" or
                    not isinstance(witnesses, list) or len(witnesses) != 1 or
                    not isinstance(witnesses[0], dict) or not witnesses[0].get(side)):
                raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Решение относится к неподходящему замечанию или сценарию.")
        return eligible, digest

    def _run(self, run, document, baseline, findings, decisions, baseline_sha256, clarifications):
        def on_progress(line):
            with self.lock:
                run.record(line)

        json_report = run.directory / "review.json"
        html_report = run.directory / "review.html"
        with self.lock:
            run.state = "running"
        state, error = "done", ""
        try:
            options = {"findings": findings} if findings else {}
            if decisions or clarifications:
                options.update(baseline_sha256=baseline_sha256)
            if decisions:
                options.update(decisions=decisions)
            if clarifications:
                options.update(clarifications=clarifications)
            self.worker(run.mode, document, baseline, json_report, html_report, self.model, on_progress, **options)
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

    def reply(self, status, body, content_type, download=None, report_digest=False):
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
        if report_digest:
            self.send_header("X-Report-SHA256", sha256(body).hexdigest())
        try:
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def json(self, status, response):
        self.reply(status, json.dumps(response, ensure_ascii=False).encode(), REPORT_FILES["review.json"])

    def body_size(self, limit, size_message="Превышен допустимый размер запроса."):
        sizes = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(sizes) != 1 or not re.fullmatch(r"[0-9]{1,10}", sizes[0]):
            raise WorkbenchError(HTTPStatus.LENGTH_REQUIRED, "Укажите размер запроса.")
        size = int(sizes[0])
        if not size:
            raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Запрос пуст.")
        if size > limit:
            raise WorkbenchError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, size_message)
        return size

    def do_GET(self):
        try:
            route = self.route()
            app = self.server.app
            if route.path in STATIC_FILES:
                artifact = app.root / route.path[1:]
                self.reply(HTTPStatus.OK, artifact.read_bytes(), STATIC_FILES[route.path])
                return
            if route.path == "/":
                self.reply(HTTPStatus.OK, (app.root / "reviewer" / PAGE.name).read_bytes(),
                           REPORT_FILES["review.html"])
                return
            status = re.fullmatch(rf"/api/status/({JOB_ID})", route.path)
            if status:
                self.json(HTTPStatus.OK, app.status(status[1]))
                return
            executions = re.fullmatch(rf"/reports/(demo|{JOB_ID})/executions/([0-9a-f]{{64}})", route.path)
            if executions:
                self.json(HTTPStatus.OK, app.execution_panels(*executions.groups()))
                return
            dialogue = re.fullmatch(DIALOGUE_ROUTE, route.path)
            if dialogue:
                self.json(HTTPStatus.OK, {"entries": app.conversations(*dialogue.groups()),
                                          "sample": app.saved_example(*dialogue.groups())})
                return
            history = re.fullmatch(DIALOGUE_ROUTE + rf"/({FINDING_ID})/({JOB_ID})\.json", route.path)
            if history:
                baseline, sha, fid, token = history.groups()
                entries = app.conversations(baseline, sha)
                tokens = next((e["history_tokens"] for e in entries if e["record"]["finding_id"] == fid), [])
                if token not in tokens:
                    raise WorkbenchError(HTTPStatus.NOT_FOUND, "Ход обсуждения не найден.")
                body = (app.storage / "clarifications" / (token + ".json")).read_bytes()
                self.reply(HTTPStatus.OK, body, REPORT_FILES["review.json"],
                           f"owner-{fid}-turn-{tokens.index(token) + 1}.json")
                return
            sample = re.fullmatch(r"/reports/demo/owner/(dialogue|wrong-revision)\.html", route.path)
            if sample:
                path = app.reports("demo") / "owner" / (sample[1] + ".html")
                self.reply(HTTPStatus.OK, path.read_bytes(), REPORT_FILES["review.html"])
                return
            report = re.fullmatch(rf"/reports/(demo|{JOB_ID})/(review\.(?:html|json))", route.path)
            if report:
                name = report[2]
                directory = app.reports(report[1])
                download = name if parse_qs(route.query).get("download") == ["1"] else None
                self.reply(HTTPStatus.OK, (directory / name).read_bytes(), REPORT_FILES[name], download,
                           report_digest=name == "review.json")
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
            if route.path == "/api/import-report":
                if route.query or self.headers.get_content_type() != "application/json":
                    raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Передайте сохранённый отчёт с типом application/json.")
                length = self.body_size(IMPORT_LIMIT, "Размер отчёта превышает 8 МиБ.")
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Получен неполный JSON-файл.")
                result = self.server.app.import_report(raw)
                self.json(HTTPStatus.OK if result["reused"] else HTTPStatus.CREATED, result)
                return
            simulation = re.fullmatch(rf"/api/simulate/(demo|{JOB_ID})/([0-9a-f]{{64}})/({FINDING_ID})", route.path)
            if simulation:
                from .executable import simulate
                from .execution_view import explain_simulation
                if route.query or self.headers.get_content_type() != "application/json":
                    raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Нужен JSON с входными значениями примера.")
                baseline, digest, fid = simulation.groups()
                raw = (self.server.app.reports(baseline, baseline=True) / "review.json").read_bytes()
                if sha256(raw).hexdigest() != digest:
                    raise WorkbenchError(HTTPStatus.CONFLICT, "Базовый отчёт изменился. Обновите страницу.")
                try:
                    data = json.loads(self.rfile.read(self.body_size(64_000)))
                    if type(data) is not dict or set(data) != {"case"}:
                        raise ValueError("Укажите только начальные значения и события в поле case")
                    report = json.loads(raw)
                    trace = simulate(report, fid, data["case"])
                    why = explain_simulation(report, fid, data["case"], trace)
                except (ValueError, KeyError, TypeError, RecursionError) as exc:
                    raise WorkbenchError(HTTPStatus.UNPROCESSABLE_ENTITY, f"Не удалось рассчитать пример. {str(exc)[:240]}") from exc
                self.json(HTTPStatus.OK, {"trace": trace, "explanation_html": why})
                return
            dialogue = re.fullmatch(DIALOGUE_ROUTE, route.path)
            if dialogue:
                if self.headers.get_content_type() != "application/json":
                    raise WorkbenchError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Передайте ответ в формате JSON.")
                raw = self.rfile.read(self.body_size(24000))
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeError, ValueError, RecursionError) as exc:
                    raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Не удалось прочитать ответ.") from exc
                self.json(HTTPStatus.OK, self.server.app.answer(*dialogue.groups(), payload))
                return
            if route.path != "/api/runs":
                raise WorkbenchError(HTTPStatus.NOT_FOUND, "Страница не найдена.")
            query = parse_qs(route.query, keep_blank_values=True)
            modes = query.get("mode", [])
            baselines = query.get("baseline", [])
            findings = tuple(query.get("finding", []))
            decisions = tuple(query.get("decision", []))
            tokens = tuple(query.get("clarification", []))
            snapshot = query.get("dialogue_snapshot", [])
            hashes = query.get("baseline_sha256", [])
            if modes == ["full"] and not baselines and set(query) == {"mode"}:
                baseline = None
            elif (modes == ["recheck"] and len(baselines) == 1 and
                  set(query) <= {"mode", "baseline", "finding", "decision", "baseline_sha256", "clarification", "dialogue_snapshot"}
                  and re.fullmatch(rf"demo|{JOB_ID}", baselines[0])):
                baseline = baselines[0]
            else:
                raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Укажите mode=full или mode=recheck с baseline.")
            if len(findings) != len(set(findings)) or any(not re.fullmatch(FINDING_ID, fid) for fid in findings):
                raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Укажите уникальные идентификаторы замечаний вида D-01.")
            decision_ids = {item.partition("=")[0] for item in decisions}
            if (len(decision_ids) != len(decisions) or
                    any(not re.fullmatch(rf"{FINDING_ID}=(?:first|second)", item) for item in decisions) or
                    len(tokens) != len(set(tokens)) or any(not re.fullmatch(JOB_ID, t) for t in tokens) or
                    (snapshot and snapshot != ["1"]) or
                    ((decisions or tokens or snapshot) and (len(hashes) != 1 or not re.fullmatch(r"[0-9a-f]{64}", hashes[0]))) or
                    (hashes and not (decisions or tokens or snapshot))):
                raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Для решений нужны уникальные D-ID, исходы и SHA-256 базового отчёта.")
            names = self.headers.get_all("X-File-Name", [])
            if len(names) != 1:
                raise WorkbenchError(HTTPStatus.BAD_REQUEST, "Укажите одно имя файла в X-File-Name.")
            name = upload_name(names[0])
            size = self.body_size(INPUT_LIMITS[Path(name).suffix.lower()])
            decision_options = ({"decisions": decisions, "dialogue_tokens": tokens, "baseline_sha256": hashes[0],
                                 "dialogue_snapshot": bool(snapshot)} if hashes else {})
            identifier = self.server.app.start(modes[0], baseline, name, size, self.rfile, findings, **decision_options)
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
