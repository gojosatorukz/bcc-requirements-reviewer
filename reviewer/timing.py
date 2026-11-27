"""Profile a saved run without making a model request."""

import argparse
from collections import defaultdict
from datetime import datetime
import json
from math import isfinite
from pathlib import Path


def group_name(stage):
    for prefix in ("Блоки ", "Поля ответов ", "Основания примера ", "Построение примера ", "Проверка смысла "):
        if stage.startswith(prefix):
            return prefix.strip()
    return stage


def duration(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value >= 0


def active_window(intervals):
    """Measure time with at least one model call and the largest simultaneous in-flight count."""
    edges = sorted((time, delta) for start, end in intervals if end > start
                   for time, delta in [(start, 1), (end, -1)])
    active = peak = 0
    total = 0.0
    previous = None
    for time, delta in edges:
        if active:
            total += time - previous
        active += delta
        peak = max(peak, active)
        previous = time
    return round(total, 2), peak


def profile(run):
    wall = run["elapsed_seconds"]
    if not duration(wall):
        raise ValueError("Некорректная длительность прогона")
    groups = defaultdict(list)
    restored = 0
    for attempt in run["attempts"]:
        if attempt.get("restored"):
            restored += 1
        else:
            groups[group_name(attempt.get("stage", "Обращение к модели"))].append(attempt)

    rows, all_intervals = [], []
    count = sum(map(len, groups.values()))
    for name, attempts in groups.items():
        seconds = [a.get("seconds") for a in attempts]
        measured = all(duration(s) for s in seconds)
        intervals = []
        for attempt, value in zip(attempts, seconds):
            stamp = attempt.get("started_utc")
            if not stamp or not duration(value):
                continue
            start = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if start.tzinfo is None:
                continue
            intervals.append((start.timestamp(), start.timestamp() + value))
        all_intervals.extend(intervals)
        rows.append({"stage": name, "calls": len(attempts),
                     "rejected": sum(a["status"] == "rejected" for a in attempts),
                     "failed": sum(a["status"] == "failed" for a in attempts),
                     "call_seconds": round(sum(seconds), 2) if measured else None,
                     "active_seconds": active_window(intervals)[0] if len(intervals) == len(attempts) else None})
    active, peak = active_window(all_intervals) if len(all_intervals) == count else (None, None)
    return {"wall_seconds": wall, "fresh_calls": count, "restored_attempts": restored,
            "model_active_seconds": active, "peak_concurrency": peak, "stages": rows}


def main():
    parser = argparse.ArgumentParser(description="Профиль времени по сохранённому JSON ревью")
    parser.add_argument("report", type=Path)
    parser.add_argument("--json", action="store_true", help="Машиночитаемый профиль")
    args = parser.parse_args()
    try:
        result = profile(json.loads(args.report.read_text(encoding="utf-8"))["run"])
    except (OSError, KeyError, ValueError, TypeError, AttributeError) as exc:
        parser.exit(2, f"Не удалось прочитать паспорт времени. {exc}\n")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"До готового отчёта {result['wall_seconds']:.2f} с. "
          f"Свежих вызовов {result['fresh_calls']}, восстановленных попыток {result['restored_attempts']}.")
    if result["peak_concurrency"] is None:
        print("Паспорт этого прогона не содержит полного набора временных меток начала вызовов.")
    else:
        print(f"Время хотя бы с одним активным вызовом {result['model_active_seconds']:.2f} с. "
              f"Одновременных вызовов на пике {result['peak_concurrency']}.")
    print("Этап | Вызовы | Отклонены | Сбой | Сумма вызовов, с | Хотя бы один активен, с")
    for row in result["stages"]:
        print(" | ".join("—" if v is None else str(v) for v in row.values()))


if __name__ == "__main__":
    main()
