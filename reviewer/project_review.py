"""Run an explicitly requested full review with the project's public service."""

from http.client import HTTPException
from http.cookiejar import CookieJar
import json
import re
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .serve import IMPORT_LIMIT, JOB_ID, SafeWorkerError, WorkbenchError, imported_full_report


ORIGIN = "https://razbor-tz.onrender.com"
TIME_LIMIT = 32 * 60
NETWORK_ERRORS = (OSError, HTTPException, URLError)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def json_object(raw):
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        raise SafeWorkerError("Сервер проекта вернул нечитаемый ответ.") from exc
    if not isinstance(value, dict):
        raise SafeWorkerError("Сервер проекта вернул ответ неизвестного формата.")
    return value


def request(client, path, *, deadline, limit, mime, data=None, headers=None):
    req = Request(ORIGIN + path, data=data, headers=headers or {})
    for attempt in range(3):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise SafeWorkerError("Истекло время ожидания ответа от сервера проекта.")
        try:
            with client.open(req, timeout=min(60, remaining)) as response:
                if response.headers.get_content_type() != mime:
                    raise SafeWorkerError("Сервер проекта вернул ответ в неожиданном формате.")
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise SafeWorkerError("Ответ сервера проекта превышает допустимый размер.")
                return raw
        except HTTPError as exc:
            exc.close()
            if exc.code == 429:
                raise SafeWorkerError("Сервер проекта занят или исчерпал часовой лимит. Повторите позже.") from exc
            if exc.code in (401, 404):
                raise SafeWorkerError("Прогон или его сессия на сервере проекта недоступны. Запустите новое ревью.") from exc
            if exc.code not in (502, 503, 504) or data is not None or attempt == 2:
                raise SafeWorkerError(f"Сервер проекта не принял запрос (HTTP {exc.code}).") from exc
        except NETWORK_ERRORS as exc:
            if data is not None:
                # Retrying a POST after losing its receipt could start a second billable review.
                raise SafeWorkerError("Не удалось получить подтверждение загрузки от сервера проекта. "
                                      "Проверьте соединение. Повторная отправка запускается вручную.") from exc
            if attempt == 2:
                raise SafeWorkerError("Не удаётся связаться с сервером проекта. Проверьте соединение и повторите позже.") from exc
        sleep(min(3 * (attempt + 1), max(0, deadline - monotonic())))


def project_worker(mode, document, baseline, json_report, html_report, model, progress, *, on_coverage=None):
    deadline = monotonic() + TIME_LIMIT
    client = build_opener(ProxyHandler({}), NoRedirect(), HTTPCookieProcessor(CookieJar()))
    progress("Подключаемся к серверу проекта")
    # The landing page creates an isolated, ephemeral visit. Never reuse browser credentials.
    request(client, "/", deadline=deadline, limit=1024 * 1024, mime="text/html")
    headers = {"Origin": ORIGIN, "Content-Type": "application/octet-stream",
               "X-File-Name": quote(document.name, safe="")}
    receipt = request(client, "/api/runs?mode=full", deadline=deadline, limit=8192, mime="application/json",
                      data=document.read_bytes(), headers=headers)
    identifier = json_object(receipt).get("id")
    if not isinstance(identifier, str) or not re.fullmatch(JOB_ID, identifier):
        raise SafeWorkerError("Сервер проекта не подтвердил номер прогона.")
    progress("Документ принят сервером проекта")
    seen = set()
    while True:
        status = json_object(request(client, "/api/status/" + identifier, deadline=deadline,
                                     limit=64 * 1024, mime="application/json"))
        state = status.get("state")
        if status.get("mode") != "full" or state not in ("queued", "running", "done", "partial", "failed"):
            raise SafeWorkerError("Сервер проекта вернул неизвестный статус проверки.")
        if state == "failed":
            error = status.get("error")
            message = " ".join(error.split())[:400] if isinstance(error, str) else ""
            raise SafeWorkerError("Проверка на сервере проекта завершилась с ошибкой. " + message)
        if on_coverage:
            coverage = status.get("coverage")
            if isinstance(coverage, dict) and coverage.get("phase") in ("groups", "whole", "scenarios", "executables", "render"):
                snapshot = {"phase": coverage["phase"]}
                for key in ("checked", "total", "proofs_checked", "proofs_total"):
                    value = coverage.get(key)
                    if type(value) is int and 0 <= value <= 999999:
                        snapshot[key] = value
                on_coverage(snapshot)
            else:
                on_coverage(None)
        for line in status.get("progress", []):
            if isinstance(line, str) and 0 < len(line) <= 180 and line.isprintable() and line not in seen and len(seen) < 200:
                progress(line)
                seen.add(line)
        if state in ("done", "partial"):
            break
        sleep(min(3, max(0, deadline - monotonic())))

    progress("Сверяем полученный отчёт с исходным документом")
    raw = request(client, f"/reports/{identifier}/review.json", deadline=deadline,
                  limit=IMPORT_LIMIT, mime="application/json")
    if state == "partial":
        from .input import load_document
        from .partial import build_partial, render_partial, valid_reserve
        from .openai_provider import MODEL_ID
        report = json_object(raw)
        model = report.get("attempted_model")
        direct = report.get("reason") == "openai_quota"
        pattern = MODEL_ID if direct else re.compile(r"gemini-[a-z0-9.-]{1,100}")
        if not isinstance(model, str) or not pattern.fullmatch(model):
            raise SafeWorkerError("Неизвестная модель в частичном отчёте сервера проекта.")
        lines, digest, metadata = load_document(document)
        reserve = report.get("attempted_openai_reserve")
        if reserve is not None and (direct or not valid_reserve(reserve)):
            raise SafeWorkerError("Неизвестный резерв в частичном отчёте сервера проекта.")
        expected = build_partial(document.name, lines, digest, metadata, model, reserve=reserve, openai_quota=direct)
        if report != expected:
            raise SafeWorkerError("Частичный отчёт сервера не совпадает с проверкой исходного документа.")
        json_report.write_bytes(raw)
        html_report.write_text(render_partial(expected), encoding="utf-8")
        return "partial"
    try:
        _, html = imported_full_report(raw, source=document)
    except WorkbenchError as exc:
        raise SafeWorkerError("Полученный отчёт не прошёл локальную проверку. " + str(exc)) from exc
    json_report.write_bytes(raw)
    html_report.write_bytes(html)
    progress("Отчёты JSON и HTML сохранены локально")
