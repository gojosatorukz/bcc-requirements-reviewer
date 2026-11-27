"""Opt-in OpenAI Responses API transport for the reviewer's strict schemas."""

from http.client import HTTPException
import json
import os
import re
import sys
from random import uniform
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .contract import wire_schema


ENDPOINT = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-6.2-sol"
MODEL_ID = re.compile(r"gpt-[a-z0-9][a-z0-9.-]{0,95}")
RESPONSE_LIMIT = 4 * 1024 * 1024
OUTPUT_LIMIT = 16384
TEMPORARY_LIMITS = ("rate_limit_exceeded", "slow_down")
BILLING_LIMITS = ("insufficient_quota", "credit_balance_exhausted", "organization_spend_limit_exceeded",
                  "project_spend_limit_exceeded", "organization_usage_limit_exceeded")
RETRY_PROGRESS = "OpenAI API попросил подождать. Одна повторная попытка в течение 30 секунд."


class QuotaExceeded(ValueError):
    """A rate or spending limit returned by the OpenAI API."""
    def __init__(self, status, reason):
        super().__init__(f"OpenAI API: HTTP {status} {reason}")
        self.reason = reason


def quota_error(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, QuotaExceeded):
            return exc
        seen.add(id(exc))
        exc = exc.__cause__
    return None


def output_budget(prompt):
    """Reserve room for reasoning and JSON in proportion to the size of the stage."""
    raw = os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "").strip()
    if raw and (not re.fullmatch(r"[0-9]{1,5}", raw) or not 1024 <= int(raw) <= OUTPUT_LIMIT):
        raise ValueError("OpenAI API: OPENAI_MAX_OUTPUT_TOKENS должен быть от 1024 до 16384")
    ceiling = int(raw) if raw else OUTPUT_LIMIT
    for characters, budget in ((10000, 4096), (35000, 8192)):
        if len(prompt) <= characters:
            return min(ceiling, budget)
    return ceiling


def diagnostic_code(error):
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    if isinstance(code, str) and code:
        return code
    return error.get("type") if isinstance(error.get("type"), str) else None


def billing_reason(code):
    if code in BILLING_LIMITS:
        return "credit_balance_exhausted" if code == "credit_balance_exhausted" else "insufficient_quota"
    return None


def retry_seconds(headers):
    raw = headers.get("Retry-After") if headers else None
    scale = 1
    if raw is None and headers:
        raw = headers.get("retry-after-ms")
        scale = 1000
    if raw is None:
        return uniform(1, 2)
    if not re.fullmatch(r"[0-9]{1,8}(?:\.[0-9]{1,4})?", raw.strip()):
        return None
    delay = float(raw) / scale
    return min(30, delay + uniform(0, .2)) if delay <= 30 else None


def request_json(body, key):
    request = Request(ENDPOINT, data=json.dumps(body, ensure_ascii=False).encode(),
                      headers={"Content-Type": "application/json", "Accept": "application/json"})
    request.add_unredirected_header("Authorization", "Bearer " + key)
    for attempt in range(2):
        try:
            with urlopen(request, timeout=180) as response:
                raw = response.read(RESPONSE_LIMIT + 1)
            break
        except HTTPError as exc:
            with exc:
                if exc.code not in (402, 429):
                    raise ValueError(f"OpenAI API: HTTP {exc.code}") from None
                try:
                    diagnostic = diagnostic_code(json.loads(exc.read(8192))["error"])
                except (KeyError, ValueError, TypeError, OSError, HTTPException):
                    diagnostic = None
            if reason := billing_reason(diagnostic):
                raise QuotaExceeded(exc.code, reason) from None
            if exc.code == 402:
                raise ValueError("OpenAI API: HTTP 402") from None
            if not attempt and diagnostic in TEMPORARY_LIMITS and (delay := retry_seconds(exc.headers)) is not None:
                print(RETRY_PROGRESS, file=sys.stderr, flush=True)
                sleep(delay)
                continue
            raise QuotaExceeded(429, "rate_limit") from None
        except (TimeoutError, URLError, OSError, HTTPException):
            raise ValueError("OpenAI API: модель не ответила") from None
    if len(raw) > RESPONSE_LIMIT:
        raise ValueError("OpenAI API: ответ превышает допустимый размер")
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("OpenAI API: повреждён ответ сервиса") from None


def complete(prompt, model, effort, schema):
    if not isinstance(model, str) or not MODEL_ID.fullmatch(model):
        raise ValueError("OpenAI API: укажите идентификатор GPT-модели в настройке модели")
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key or not re.fullmatch(r"[!-~]{1,512}", key):
        raise ValueError("OpenAI API: проверьте настройку OPENAI_API_KEY на сервере")
    body = {"model": model, "input": prompt, "store": False, "tools": [],
            "reasoning": {"effort": effort}, "max_output_tokens": output_budget(prompt),
            "text": {"format": {"type": "json_schema", "name": "review_stage", "strict": True, "schema": wire_schema(schema)}}}
    response = request_json(body, key)
    if isinstance(response, dict) and response.get("status") == "failed":
        code = diagnostic_code(response.get("error"))
        if reason := billing_reason(code):
            raise QuotaExceeded(200, reason)
        if code in TEMPORARY_LIMITS:
            raise QuotaExceeded(200, "rate_limit")
    if (isinstance(response, dict) and response.get("status") == "incomplete"
            and response.get("incomplete_details") == {"reason": "max_output_tokens"}):
        raise ValueError(f"OpenAI API: ответа не хватило для полного JSON за {body['max_output_tokens']} токенов")
    try:
        if response["status"] != "completed" or not MODEL_ID.fullmatch(response["model"]):
            raise ValueError()
        messages = []
        for item in response["output"]:
            if item["type"] == "reasoning":
                continue
            if item["type"] != "message" or item["role"] != "assistant" or item["status"] != "completed":
                raise ValueError()
            parts = item["content"]
            if not parts or any(part["type"] != "output_text" for part in parts):
                raise ValueError()
            messages.append("".join(part["text"] for part in parts))
        if len(messages) != 1:
            raise ValueError()
        result = json.loads(messages[0])
        if not isinstance(result, dict):
            raise ValueError()
    except (KeyError, IndexError, TypeError, ValueError, RecursionError):
        raise ValueError("OpenAI API: не получен завершённый структурированный ответ") from None
    usage = response.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    counts = {"response_model": response["model"]}
    sources = ((usage, "input_tokens", "input_tokens"), (usage, "output_tokens", "output_tokens"),
               (usage.get("input_tokens_details"), "cached_tokens", "cached_input_tokens"),
               (usage.get("output_tokens_details"), "reasoning_tokens", "reasoning_output_tokens"))
    for source, field, name in sources:
        if isinstance(source, dict) and type(source.get(field)) is int and source[field] >= 0:
            counts[name] = source[field]
    return result, counts
