"""Read the reviewer's local configuration when a command-line entry point starts."""

import os
from pathlib import Path
import shlex

CONFIG_KEYS = {"REVIEW_PROVIDER", "REVIEW_MODEL", "GEMINI_API_KEY", "OPENAI_API_KEY", "REVIEW_FALLBACK_MODEL", "REVIEW_FALLBACK_PROVIDER",
               "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "REVIEW_CHROME"}


def load_project_env(path=None):
    path = path or Path(__file__).resolve().parent.parent / ".env"
    if not path.is_file():
        return
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key, separator, raw = line.strip().removeprefix("export ").partition("=")
        key = key.strip()
        if not separator or key not in CONFIG_KEYS:
            continue
        try:
            parts = shlex.split(raw, comments=True, posix=True)
        except ValueError:
            raise SystemExit(f"Проверьте кавычки в .env для {key}.") from None
        if len(parts) > 1:
            raise SystemExit(f"Значение {key} в .env с пробелами нужно заключить в кавычки.")
        values[key] = parts[0] if parts else ""
    # An explicit provider switch must not inherit another provider's model.
    if "REVIEW_PROVIDER" in os.environ and os.environ["REVIEW_PROVIDER"] != values.get("REVIEW_PROVIDER"):
        values.pop("REVIEW_MODEL", None)
    for key, value in values.items():
        os.environ.setdefault(key, value)
