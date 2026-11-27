"""Stateless Gemini Developer API transport; the caller verifies the original stage contract."""

from http.client import HTTPException
import json
from math import ceil
import os
import re
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .contract import wire_schema


ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/"
RESPONSE_LIMIT = 4 * 1024 * 1024


def usage_counts(metadata):
    if not isinstance(metadata, dict):
        return None
    fields = {"input_tokens": "promptTokenCount", "cached_input_tokens": "cachedContentTokenCount",
              "reasoning_output_tokens": "thoughtsTokenCount"}
    counts = {local: metadata[remote] for local, remote in fields.items()
              if type(metadata.get(remote)) is int and metadata[remote] >= 0}
    # Codex's output counter includes reasoning, shown separately as a subset.
    visible = metadata.get("candidatesTokenCount")
    if type(visible) is int and visible >= 0:
        counts["output_tokens"] = visible + counts.get("reasoning_output_tokens", 0)
    return counts or None


class SchemaRejected(ValueError):
    """The API rejected the wire schema before generation."""


class QuotaExceeded(ValueError):
    """The configured Gemini endpoint returned HTTP 429."""


def quota_wait(exc):
    try:
        details = json.loads(exc.read(32_768))["error"]["details"]
        violations = [v["quotaId"] for entry in details for v in entry.get("violations", ())]
        if not violations or not all("PerMinute" in name for name in violations):
            return None
        for entry in details:
            match = re.fullmatch(r"([0-9]{1,2}(?:\.[0-9]{1,9})?)s", entry.get("retryDelay", ""))
            if match and float(match[1]) <= 60:
                return max(1, ceil(float(match[1])))
    except (KeyError, TypeError, AttributeError, ValueError, OSError, HTTPException):
        pass
    return None


def open_generation(request):
    try:
        return urlopen(request, timeout=120)
    except HTTPError as exc:
        delay = None
        if exc.code == 429:
            delay = quota_wait(exc)
        elif exc.code == 503:
            after = (exc.headers or {}).get("Retry-After", "2").strip()
            if re.fullmatch(r"[0-9]{1,2}", after) and int(after) <= 10:
                delay = int(after)
        if delay is None:
            raise
        exc.close()
        sleep(delay)
    return urlopen(request, timeout=120)


def request_json(model, key, body):
    request = Request(ENDPOINT + model + ":generateContent", method="POST",
                      data=json.dumps(body, ensure_ascii=False).encode(),
                      headers={"Content-Type": "application/json", "Accept": "application/json"})
    request.add_unredirected_header("x-goog-api-key", key)
    try:
        with open_generation(request) as response:
            raw = response.read(RESPONSE_LIMIT + 1)
    except HTTPError as exc:
        # Provider diagnostics may contain the submitted document. Only fixed messages escape.
        try:
            diagnostic = exc.read(32_768).decode("utf-8", errors="replace").lower() if exc.code == 400 else ""
        finally:
            exc.close()
        if exc.code == 400 and ("api_key_invalid" in diagnostic or "api key not valid" in diagnostic):
            raise ValueError("Gemini API: ключ отклонён") from None
        if (exc.code == 400 and ("schema" in diagnostic or "too many states" in diagnostic)
                and any(word in diagnostic for word in ("invalid", "complex", "not supported", "unsupported",
                                                       "too many", "too deep", "nesting", "constraint"))):
            raise SchemaRejected("Gemini API: схема слишком сложна для структурированного вывода") from None
        if exc.code == 429:
            raise QuotaExceeded("Gemini API: HTTP 429 RESOURCE_EXHAUSTED") from None
        raise ValueError(f"Gemini API: HTTP {exc.code}") from None
    except (TimeoutError, URLError, OSError, HTTPException):
        raise ValueError("Gemini API: модель не ответила") from None
    if len(raw) > RESPONSE_LIMIT:
        raise ValueError("Gemini API: ответ превышает допустимый размер")
    try:
        envelope = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Gemini API: повреждён ответ сервиса") from None
    if not isinstance(envelope, dict):
        raise ValueError("Gemini API: повреждён ответ сервиса")
    return envelope


def complete(prompt, model, effort, schema):
    if not re.fullmatch(r"gemini-[a-z0-9][a-z0-9.-]{0,79}", model):
        raise ValueError("Gemini API: укажите идентификатор модели gemini-* в REVIEW_MODEL или --model")
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key or not key.strip():
        raise ValueError(
            "Gemini API: ключ не настроен. Укажите GEMINI_API_KEY в .env. "
            "Без своего ключа запустите python -m reviewer.serve и выберите «Проверить через сервер проекта»."
        )
    key = key.strip()
    if not re.fullmatch(r"[!-~]{1,512}", key):
        raise ValueError("Gemini API: ключ отклонён")
    thinking = ({"thinkingBudget": 1024 if effort == "low" else 4096} if model.startswith("gemini-2.5-")
                else {"thinkingLevel": effort.upper()})
    config = {"responseMimeType": "application/json", "responseJsonSchema": wire_schema(schema), "thinkingConfig": thinking}
    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    body = {"contents": contents, "generationConfig": config}
    try:
        envelope = request_json(model, key, body)
    except SchemaRejected:
        # Deep contracts can exceed Google's grammar compiler limits. Keep the same
        # model and full schema in the instruction; local shape and citation checks still run.
        del config["responseJsonSchema"]
        contents[0]["parts"].append({"text": "Верни JSON строго по этой схеме:\n" +
                                     json.dumps(schema, ensure_ascii=False, separators=(",", ":"))})
        envelope = request_json(model, key, body)

    candidates = envelope.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], dict):
        raise ValueError("Gemini API: модель не вернула ответ")
    candidate = candidates[0]
    if candidate.get("finishReason") != "STOP":
        raise ValueError("Gemini API: модель не завершила ответ")
    content = candidate.get("content")
    parts = content.get("parts") if isinstance(content, dict) else None
    if (not isinstance(parts, list) or not parts
            or any(not isinstance(part, dict) or not isinstance(part.get("text"), str)
                   or any(field in part for field in ("functionCall", "executableCode", "codeExecutionResult")) for part in parts)):
        raise ValueError("Gemini API: ожидался текстовый ответ")
    text = "".join(part["text"] for part in parts if not part.get("thought"))
    try:
        result = json.loads(text)
    except ValueError:
        raise ValueError("Gemini API: модель вернула некорректный JSON") from None
    return result, usage_counts(envelope.get("usageMetadata"))
