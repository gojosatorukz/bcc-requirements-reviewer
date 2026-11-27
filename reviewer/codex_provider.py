"""Read a Codex CLI structured response in a fresh temporary working directory."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import NamedTuple

from .contract import REVIEW


class ModelOptions(NamedTuple):
    name: str
    effort: str


def complete(prompt, model, output_schema=REVIEW):
    name, effort = model if isinstance(model, ModelOptions) else (model, "medium")
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
        env = {k: v for k, v in os.environ.items() if k not in {"CODEX_THREAD_ID", "CODEX_INTERNAL_ORIGINATOR_OVERRIDE"}}
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
