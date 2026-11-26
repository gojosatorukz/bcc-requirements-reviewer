"""Verify stored semantic-review controls or re-run the production semantic auditor on the same programs."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import monotonic


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))

from reviewer.contract import source_lines
from reviewer.executable import AUDIT, AUDITOR, check_audit, targets
from reviewer.full import call_stage
from reviewer.witness_vm import execute


def check_case(row, lines, witnesses, model, fresh):
    fid = row["finding_id"]
    witness = witnesses[fid]
    program = row["program"]
    replay = execute(program, lines, witness)
    if replay != row["result"]:
        raise ValueError(f"{row['case']}: изменилась воспроизводимая трасса")
    if fresh:
        payload = {"source": lines, "witness": witness, "program": program, "computed": replay}
        started = monotonic()
        audit, attempts = call_stage("Проверка смысла " + fid, AUDITOR, payload, AUDIT,
                                     lambda a: check_audit(a, witness, lines), model, None, "audit-eval")
        row = dict(row, audit=audit, attempts=attempts, seconds=round(monotonic() - started, 2),
                   prompt_sha256=sha256(AUDITOR.encode()).hexdigest())
    check_audit(row["audit"], witness, lines)
    return row


def main():
    parser = argparse.ArgumentParser(description="Проверить основание расчёта на пяти программах")
    parser.add_argument("--live", type=Path, help="Повторить пять проверок моделью и сохранить в новый файл")
    args = parser.parse_args()
    if args.live and args.live.exists():
        parser.error("Выберите новый файл для свежих ответов")
    data = json.loads((HERE / "results.json").read_bytes())
    raw = (ROOT / "runs" / "bcc-submission" / "review.json").read_bytes()
    if sha256(raw).hexdigest() != data["report_sha256"]:
        parser.error("Требуется исходный официальный отчёт")
    report = json.loads(raw)
    lines, witnesses = source_lines(report), targets(report)

    def inspect(row):
        return check_case(row, lines, witnesses, data["model"], args.live is not None)

    with ThreadPoolExecutor(max_workers=2) as workers:
        rows = list(workers.map(inspect, data["cases"]))
    if args.live:
        with args.live.open("x", encoding="utf-8") as output:
            json.dump(dict(data, cases=rows), output, indent=2, ensure_ascii=False)
            output.write("\n")
    for row in rows:
        print(f"{row['case']} · ожидалось {row['expected']} · получено {row['audit']['verdict']}")
    return int(any(row["audit"]["verdict"] != row["expected"] for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
