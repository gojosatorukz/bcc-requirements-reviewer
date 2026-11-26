import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from . import codex_provider
from .contract import INSTRUCTIONS, validate_review


def version():
    root = Path(__file__).resolve().parent.parent
    try:
        head = subprocess.check_output(["git", "rev-parse", "--short=12", "HEAD"], cwd=root,
                                       stderr=subprocess.DEVNULL, text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain", "--", "reviewer"], cwd=root, text=True)
        return head + ("+modified" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unversioned"


def write_report(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as temp:
        name = temp.name
        try:
            json.dump(value, temp, ensure_ascii=False, indent=2)
            temp.write("\n")
            temp.flush()
            temp.close()
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Поиск смысловых противоречий в требованиях")
    parser.add_argument("document", type=Path)
    parser.add_argument("report", type=Path, help="Выходной файл JSON")
    parser.add_argument("--model", default="gpt-6.2-astra")
    args = parser.parse_args()
    try:
        if args.document.resolve() == args.report.resolve():
            raise ValueError("Пути документа и отчёта должны различаться")
        if args.document.suffix.lower() not in {".md", ".txt"} or args.report.suffix.lower() != ".json":
            raise ValueError("Ожидаются вход .md/.txt и выход .json")
        raw = args.document.read_bytes()
        lines = raw.decode("utf-8-sig").splitlines()
        blocks = [{"line": n, "text": line} for n, line in enumerate(lines, 1) if line.strip()]
        if not blocks:
            raise ValueError("Документ пуст")
        if len(raw) > 120_000:
            raise ValueError("Этот режим принимает до 120 000 байт за один запрос")
        started = datetime.now(timezone.utc).isoformat()
        clock = time.monotonic()
        prompt = INSTRUCTIONS + "\n\nДокумент:\n" + json.dumps(blocks, ensure_ascii=False)
        result, usage = codex_provider.complete(prompt, args.model)
        validate_review(result, lines)
        run = {"started_utc": started, "elapsed_seconds": round(time.monotonic() - clock, 2),
               "provider": "codex-cli", "model": args.model, "agent_version": version(),
               "input_name": args.document.name, "input_sha256": hashlib.sha256(raw).hexdigest(),
               "input_lines": len(lines), "usage": usage}
        report = {"schema_version": 1, "scope": "Проверка поведенческих противоречий",
                  "run": run, "source": blocks, "conflicts": result["conflicts"]}
        write_report(args.report, report)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Прогон не завершён: {exc}\n")
    print(f"Отчёт сохранён: {args.report}. Конфликтов: {len(result['conflicts'])}")


if __name__ == "__main__":
    main()
