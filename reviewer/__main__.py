import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
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


def review_document(prompt, model, lines):
    """Allow one fresh attempt to repair an invalid model response."""
    attempts = []
    next_prompt = prompt
    for attempt in range(2):
        result, usage = codex_provider.complete(next_prompt, model)
        try:
            validate_review(result, lines)
        except ValueError as exc:
            if attempt:
                raise
            attempts.append({"status": "rejected", "reason": str(exc), "usage": usage})
            next_prompt = (prompt + "\n\nПредыдущая попытка отклонена при проверке структуры или цитаты: "
                           + str(exc) + ". Повторно проверь документ и составь полный ответ заново.")
        else:
            attempts.append({"status": "accepted", "usage": usage})
            return result, attempts


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
        with args.document.open("rb") as stream:
            raw = stream.read(120_001)
        if len(raw) > 120_000:
            raise ValueError("Этот режим принимает до 120 000 байт за один запрос")
        lines = raw.decode("utf-8-sig").splitlines()
        blocks = [{"line": n, "text": line} for n, line in enumerate(lines, 1) if line.strip()]
        if not blocks:
            raise ValueError("Документ пуст")
        revision = version()
        started = datetime.now(timezone.utc).isoformat()
        clock = time.monotonic()
        prompt = INSTRUCTIONS + "\n\nДокумент:\n" + json.dumps(blocks, ensure_ascii=False)
        result, attempts = review_document(prompt, args.model, lines)
        usage = None
        if all(isinstance(attempt["usage"], dict) for attempt in attempts):
            usage = {}
            for attempt in attempts:
                for key, value in attempt["usage"].items():
                    if type(value) is int:
                        usage[key] = usage.get(key, 0) + value
        run = {"started_utc": started, "elapsed_seconds": round(time.monotonic() - clock, 2),
               "provider": "codex-cli", "model": args.model, "agent_version": revision,
               "input_name": args.document.name, "input_sha256": hashlib.sha256(raw).hexdigest(),
               "input_lines": len(lines), "usage": usage, "attempts": attempts}
        report = {"schema_version": 1, "scope": "Проверка поведенческих противоречий",
                  "run": run, "source": blocks, "conflicts": result["conflicts"]}
        write_report(args.report, report)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Прогон не завершён: {exc}\n")
    print(f"Отчёт сохранён: {args.report}. Конфликтов: {len(result['conflicts'])}")


if __name__ == "__main__":
    main()
