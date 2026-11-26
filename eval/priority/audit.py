"""Verify the six independent reports and print the manually matched finding families."""

from collections import Counter
import json
from pathlib import Path

from reviewer.full_render import validate_report


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LABELS = {"критический": "К", "существенный": "С", "редакционный": "Р"}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify():
    manifest = read(HERE / "matches.json")
    engines, starts = set(), set()
    for case in manifest:
        reports = [read(ROOT / path) for path in case["reports"]]
        if len(reports) != 3:
            raise ValueError("Для каждого ТЗ нужны три отчёта")
        findings = []
        for report in reports:
            validate_report(report)
            run = report["run"]
            if run["input_sha256"] != case["sha256"] or report["priority_policy"] != "impact-v1":
                raise ValueError("ТЗ или версия шкалы изменились")
            if not run["attempts"] or any(attempt.get("restored") for attempt in run["attempts"]):
                raise ValueError("Нужен полный прогон без восстановления стадий")
            engines.add((run["engine_sha256"], run["model"]))
            starts.add(run["started_utc"])
            findings.append({f["id"]: f["priority"] for f in report["whole"]["findings"]})

        seen = [set() for _ in reports]
        totals = Counter()
        print(f'\n{case["case"]}')
        for topic in case["topics"]:
            title, expected, cells = topic
            if len(cells) != len(reports):
                raise ValueError(f"Неверное число столбцов для {title}")
            priorities, rendered = set(), []
            appearances = 0
            for ids, inventory, assigned in zip(cells, findings, seen):
                if len(set(ids)) != len(ids) or assigned.intersection(ids):
                    raise ValueError(f"Повтор замечания в карте: {title} {ids}")
                assigned.update(ids)
                rendered.append(", ".join(f"{fid} {LABELS[inventory[fid]]}" for fid in ids) or "—")
                priorities.update(inventory[fid] for fid in ids)
                appearances += bool(ids)
                if expected:
                    totals["control_hits"] += bool(ids)
                    totals["control_slots"] += 1
                    totals["correct"] += bool(ids) and all(inventory[fid] == expected for fid in ids)
            totals["recurring"] += appearances >= 2
            totals["changed"] += appearances >= 2 and len(priorities) > 1
            totals["single"] += appearances == 1
            print(f'{title} | {" | ".join(rendered)}')
        for assigned, inventory in zip(seen, findings):
            if assigned != set(inventory):
                raise ValueError(f"Карта пропускает замечания: {set(inventory) - assigned}")
        print(f"Находок: {[len(items) for items in findings]}; {dict(totals)}")
    if len(engines) != 1 or len(starts) != 6:
        raise ValueError("Ожидались шесть разных запусков одной модели и одного движка")


if __name__ == "__main__":
    verify()
