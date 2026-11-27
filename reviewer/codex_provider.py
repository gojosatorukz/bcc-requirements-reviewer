"""Select the configured model provider for every structured review call."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import NamedTuple

from .contract import REVIEW
from . import cloudflare_provider, gemini_provider


FALLBACK_PROGRESS = "Gemini исчерпал квоту · начинаем проверку через Cloudflare Workers AI"


class ModelOptions(NamedTuple):
    name: str
    effort: str
    provider: str = ""


def provider_name(model=None):
    if isinstance(model, ModelOptions) and model.provider:
        return model.provider
    configured = os.environ.get("REVIEW_PROVIDER", "codex").strip().lower()
    if configured in {"codex", "codex-cli"}:
        return "codex-cli"
    if configured == "gemini":
        return "gemini-api"
    if configured == "cloudflare":
        return "cloudflare-workers-ai"
    raise ValueError("Неизвестный REVIEW_PROVIDER. Выберите codex, gemini или cloudflare.")


def default_model():
    configured = os.environ.get("REVIEW_MODEL", "").strip()
    if configured:
        return configured
    if provider_name() == "gemini-api":
        return "gemini-3.5-flash-lite"
    if provider_name() == "cloudflare-workers-ai":
        return cloudflare_provider.DEFAULT_MODEL
    return "gpt-6.2-astra"


def model_name(model):
    return model.name if isinstance(model, ModelOptions) else model


def fallback_model(model):
    if provider_name(model) != "gemini-api":
        return None
    name = os.getenv("REVIEW_FALLBACK_MODEL", "").strip()
    if not name or not all(os.getenv(k, "").strip() for k in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")):
        return None
    return ModelOptions(name, model.effort if isinstance(model, ModelOptions) else "medium", "cloudflare-workers-ai")


def with_quota_fallback(operation, model):
    """Restart the entire operation with one provider and its own checkpoints."""
    try:
        return operation(model), model, None
    except ValueError as exc:
        if not (isinstance(exc, gemini_provider.QuotaExceeded)
                or isinstance(exc.__cause__, gemini_provider.QuotaExceeded)):
            raise
        fallback = fallback_model(model)
        if fallback is None:
            raise
    print(FALLBACK_PROGRESS, file=sys.stderr, flush=True)
    origin = {"provider": provider_name(model), "model": model_name(model), "reason": "HTTP 429"}
    return operation(fallback), fallback, origin


def complete(prompt, model, output_schema=REVIEW):
    name = model_name(model)
    effort = model.effort if isinstance(model, ModelOptions) else "medium"
    provider = provider_name(model)
    if provider == "gemini-api":
        return gemini_provider.complete(prompt, name, effort, output_schema)
    if provider == "cloudflare-workers-ai":
        return cloudflare_provider.complete(prompt, name, effort, output_schema)
    with tempfile.TemporaryDirectory(prefix="reqreview-") as work:
        directory = Path(work)
        schema = directory / "schema.json"
        result = directory / "answer.json"
        schema.write_text(json.dumps(output_schema), encoding="utf-8")
        command = [
            "codex", "-a", "never", "exec", "--ephemeral", "--ignore-user-config",
            "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never",
            "-c", 'web_search="disabled"', "-c", "project_doc_max_bytes=0",
            "-c", f'model_reasoning_effort="{effort}"', "--disable", "memories",
            "--disable", "apps", "--disable", "plugins", "--disable", "shell_tool",
            "--disable", "unified_exec", "--disable", "multi_agent", "--disable", "multi_agent_v2",
            "--model", name, "--json", "--output-schema", str(schema), "-o", str(result), "-",
        ]
        env = {k: v for k, v in os.environ.items() if k not in {
            "CODEX_THREAD_ID", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE", "GEMINI_API_KEY", "GOOGLE_API_KEY",
            "CLOUDFLARE_API_TOKEN"}}
        try:
            proc = subprocess.run(command, cwd=work, env=env, input=prompt, capture_output=True,
                                  text=True, encoding="utf-8", timeout=480)
        except FileNotFoundError as exc:
            raise ValueError("Codex CLI не найден в PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Codex CLI не завершил запрос за 480 секунд") from exc
        if proc.returncode:
            errors = []
            for line in proc.stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "error":
                    errors.append(str(event.get("message", "")))
            detail = errors[-1][:400] if errors else proc.stderr.strip()[-400:]
            raise ValueError(f"Codex CLI завершился с кодом {proc.returncode}. {detail}")
        usage = None
        completed = False
        for line in proc.stdout.splitlines():
            event = json.loads(line)
            if event.get("type") in {"item.started", "item.completed"}:
                if event["item"]["type"] not in {"agent_message", "reasoning"}:
                    raise ValueError("Провайдер использовал инструмент. Прогон отклонён.")
            if event.get("type") == "turn.completed":
                completed = True
                usage = event.get("usage")
        if not completed or not result.is_file():
            raise ValueError("Codex CLI не подтвердил завершение прогона")
        return json.loads(result.read_text(encoding="utf-8")), usage
