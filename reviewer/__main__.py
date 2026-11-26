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


def engine_digest(*modules):
    directory = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in sorted({"__main__.py", "contract.py", "codex_provider.py", *modules}):
        content = (directory / name).read_bytes()
        digest.update(name.encode() + b"\0" + hashlib.sha256(content).digest())
    return digest.hexdigest()


def write_report(path, value):
    if path.suffix.lower() == ".html":
        from .render import render_report
        content = render_report(value)
    else:
        content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False)
    try:
        with temp:
            temp.write(content)
        os.replace(temp.name, path)
    finally:
        Path(temp.name).unlink(missing_ok=True)


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
    parser = argparse.ArgumentParser(description="Полное ревью спецификации требований")
    parser.add_argument("document", type=Path)
    parser.add_argument("report", type=Path, help="Выходной файл JSON или HTML")
    parser.add_argument("--model", default="gpt-6.2-astra")
    parser.add_argument("--conflicts-only", action="store_true", help="Быстрый поиск поведенческих конфликтов (формат v1)")
    parser.add_argument("--checkpoint-dir", type=Path, help="Папка для возобновления проверенных стадий полного прогона")
    args = parser.parse_args()
    try:
        if args.document.resolve() == args.report.resolve():
            raise ValueError("Пути документа и отчёта должны различаться")
        if args.document.suffix.lower() not in {".md", ".txt"} or args.report.suffix.lower() not in {".json", ".html"}:
            raise ValueError("Ожидаются вход .md/.txt и выход .json/.html")
        with args.document.open("rb") as stream:
            raw = stream.read(120_001)
        if len(raw) > 120_000:
            raise ValueError("Этот режим принимает до 120 000 байт за один запрос")
        lines = raw.decode("utf-8-sig").splitlines()
        blocks = [{"line": n, "text": line} for n, line in enumerate(lines, 1) if line.strip()]
        if not blocks:
            raise ValueError("Документ пуст")
        revision = version()
        digest = engine_digest() if args.conflicts_only else engine_digest("segments.py", "full.py")
        started = datetime.now(timezone.utc).isoformat()
        clock = time.monotonic()
        if args.conflicts_only:
            prompt = INSTRUCTIONS + "\n\nДокумент:\n" + json.dumps(blocks, ensure_ascii=False)
            result, attempts = review_document(prompt, args.model, lines)
        else:
            from .full import analyze
            result, attempts = analyze(blocks, lines, args.model, args.checkpoint_dir, digest)
        usage = None
        if all(isinstance(attempt["usage"], dict) for attempt in attempts):
            usage = {}
            for attempt in attempts:
                for key, value in attempt["usage"].items():
                    if type(value) is int:
                        usage[key] = usage.get(key, 0) + value
        run = {"started_utc": started, "elapsed_seconds": round(time.monotonic() - clock, 2),
               "provider": "codex-cli", "model": args.model, "agent_version": revision, "engine_sha256": digest,
               "input_name": args.document.name, "input_sha256": hashlib.sha256(raw).hexdigest(),
               "input_lines": len(lines), "usage": usage, "attempts": attempts}
        scope = "Проверка поведенческих противоречий" if args.conflicts_only else "Полное ревью требований"
        report = {"schema_version": 1 if args.conflicts_only else 2, "scope": scope, "run": run,
                  "source": blocks, **result}
        write_report(args.report, report)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Прогон не завершён: {exc}\n")
    print(f"Отчёт сохранён: {args.report}")


if __name__ == "__main__":
    main()
