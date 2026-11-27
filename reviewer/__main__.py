import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

from . import codex_provider
from .contract import HTTP_STATUS_POLICY, INSTRUCTIONS, validate_review
from .input import INPUT_LIMITS, load_document


def version():
    root = Path(__file__).resolve().parent.parent
    try:
        head = subprocess.check_output(["git", "rev-parse", "--short=12", "HEAD"], cwd=root,
                                       stderr=subprocess.DEVNULL, text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain", "--", "reviewer"], cwd=root, text=True)
        return head + ("+modified" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        try:
            revision = (root / ".image-revision").read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            return "unversioned"
        return revision[:12] if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision) else "unversioned"


def engine_digest(*modules):
    directory = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in sorted({"__main__.py", "contract.py", "codex_provider.py", *modules}):
        content = (directory / name).read_bytes()
        digest.update(name.encode() + b"\0" + hashlib.sha256(content).digest())
    return digest.hexdigest()


def write_report(path, value):
    if path.suffix.lower() == ".pdf":
        from .pdf_output import write_pdf
        from .render import render_report
        write_pdf(path, render_report(value))
        return
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
            validate_review(result, lines, http_status_policy=HTTP_STATUS_POLICY)
        except ValueError as exc:
            if attempt:
                raise
            attempts.append({"status": "rejected", "reason": str(exc), "usage": usage})
            next_prompt = (prompt + "\n\nПредыдущая попытка отклонена при проверке ответа: "
                           + str(exc) + ". Повторно проверь документ и составь полный ответ заново.")
        else:
            attempts.append({"status": "accepted", "usage": usage})
            return result, attempts


def aggregate_usage(attempts):
    totals = {}
    for attempt in attempts:
        if not isinstance(attempt["usage"], dict):
            return None
        for key, value in attempt["usage"].items():
            if type(value) is int:
                totals[key] = totals.get(key, 0) + value
    return totals


def run_usage(attempts):
    fresh = [attempt for attempt in attempts if not attempt.get("restored")]
    return {"usage": aggregate_usage(attempts), "current_usage": aggregate_usage(fresh),
            "fresh_calls": len(fresh), "restored_attempts": len(attempts) - len(fresh)}


def main():
    parser = argparse.ArgumentParser(description="Полное ревью спецификации требований")
    parser.add_argument("document", type=Path)
    parser.add_argument("report", type=Path, help="Выходной файл JSON, HTML или PDF")
    parser.add_argument("--model", default=os.environ.get("REVIEW_MODEL", "gpt-6.2-astra"))
    parser.add_argument("--effort", choices=("medium", "low"), default="medium",
                        help="Глубина рассуждения модели. В паспорте сохраняется выбранный режим.")
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=6, metavar="1..8",
                        help="Максимум параллельных запросов полного ревью (по умолчанию 6)")
    parser.add_argument("--conflicts-only", action="store_true", help="Быстрый поиск поведенческих конфликтов (формат v1)")
    parser.add_argument("--checkpoint-dir", type=Path, help="Папка для возобновления проверенных стадий полного прогона")
    args = parser.parse_args()
    try:
        if args.document.resolve() == args.report.resolve():
            raise ValueError("Пути документа и отчёта должны различаться")
        if args.document.suffix.lower() not in INPUT_LIMITS or args.report.suffix.lower() not in {".json", ".html", ".pdf"}:
            raise ValueError("Ожидаются вход .md/.txt/.pdf/.docx и выход .json/.html/.pdf")
        lines, input_hash, input_metadata = load_document(args.document)
        blocks = [{"line": n, "text": line} for n, line in enumerate(lines, 1) if line.strip()]
        if not blocks:
            raise ValueError("Документ пуст")
        revision = version()
        digest = engine_digest("input.py", "word_input.py") if args.conflicts_only else engine_digest(
            "input.py", "word_input.py", "segments.py", "json_examples.py", "example_shapes.py",
            "full.py", "source_refs.py", "lifecycle.py", "priority.py", "response_fields.py", "executable.py", "witness_vm.py")
        started = datetime.now(timezone.utc).isoformat()
        clock = time.monotonic()
        # Non-default options are part of each stage's content-addressed checkpoint key.
        model = args.model if args.effort == "medium" else codex_provider.ModelOptions(args.model, args.effort)
        if args.conflicts_only:
            prompt = INSTRUCTIONS + "\n\nДокумент:\n" + json.dumps(blocks, ensure_ascii=False)
            result, attempts = review_document(prompt, model, lines)
        else:
            from .full import analyze
            result, attempts = analyze(blocks, lines, model, args.checkpoint_dir, digest, args.workers,
                                       input_metadata.get("pdf_json_blocks", ()))
        run = {"started_utc": started, "elapsed_seconds": round(time.monotonic() - clock, 2),
               "provider": "codex-cli", "model": args.model, "reasoning_effort": args.effort,
               "agent_version": revision, "engine_sha256": digest,
               "input_name": args.document.name, "input_sha256": input_hash, **input_metadata,
               "input_lines": len(lines), **run_usage(attempts), "attempts": attempts}
        if not args.conflicts_only:
            run["max_workers"] = args.workers
        scope = "Проверка поведенческих противоречий" if args.conflicts_only else "Полное ревью требований"
        report = {"schema_version": 1 if args.conflicts_only else 2, "scope": scope, "run": run,
                  "http_status_policy": HTTP_STATUS_POLICY,
                  "source": blocks, **result}
        write_report(args.report, report)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Прогон не завершён: {exc}\n")
    print(f"Отчёт сохранён: {args.report}")


if __name__ == "__main__":
    main()
