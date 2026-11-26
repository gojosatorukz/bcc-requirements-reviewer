"""Verify actual CLI artifacts, ground citations and recompute traces from saved programs."""

import argparse
from hashlib import sha256
import json
from pathlib import Path

from reviewer.executable import targets, validate_saved
from reviewer.full_render import validate_report

from .run import CASES, HERE, MODEL, ROOT, documents


def require(valid, why):
    if not valid:
        raise ValueError(why)


def source_citations(value):
    if isinstance(value, dict):
        if "line" in value and "quote" in value:
            yield value
        for child in value.values():
            yield from source_citations(child)
    elif isinstance(value, list):
        for child in value:
            yield from source_citations(child)


def verify(name, report_path, document):
    report = json.loads(report_path.read_bytes())
    run = report["run"]
    require(report["schema_version"] == 2 and run["input_sha256"] == sha256(document.read_bytes()).hexdigest(),
            f"{name}: неправильный вход или тип отчёта")
    require(run["model"] == MODEL and run["provider"] == "codex-cli" and run["fresh_calls"] > 0
            and run["restored_attempts"] == 0 and "execution_run" not in report,
            f"{name}: ожидался свежий полный прогон обычной команды")
    source = validate_report(report)
    expected = {i: line for i, line in enumerate(document.read_text().splitlines(), 1) if line.strip()}
    require(source == expected, f"{name}: снимок строк отличается от документа")
    cites = list(source_citations(report))
    for item in cites:
        require(item["quote"] and item["quote"] in source.get(item["line"], ""),
                f"{name}: неверная цитата в строке {item['line']}")
    validate_saved(report, source)
    pairs = []
    for item in report["executables"]["items"]:
        w = targets(report)[item["finding_id"]]
        pair = {w[side]["source"]["line"] for side in ("first", "second")}
        pairs.append((pair, item))
        if item["status"] == "executed":
            result = item["result"]
            step = result["scenario"]["first_difference"]["step"]
            require(all(not row["different"] for row in result["scenario"]["steps"][:step - 1]),
                    f"{name}: пропущен более ранний расходящийся шаг")
            require(not any(row["different"] for row in result["control"]["steps"]),
                    f"{name}: контроль расходится")
    return report, pairs, len(cites)


def check_protocol(name, pairs):
    positive = {"official": ((38, 64), (39, 40)), "hotel": ((12, 16),), "fee": ((9, 13),)}
    for left, right in positive.get(name, ()):
        outcomes = [item["status"] for anchors, item in pairs if anchors == {left, right}]
        require(outcomes == ["executed"], f"{name}: строки {left} и {right} дали {outcomes}")
    if name == "aligned":
        require(not any(anchors == {12, 16} for anchors, _ in pairs),
                "aligned: согласованные К-2 и В-1 представлены как критический конфликт")
    if name == "ledger":
        for anchors, _ in pairs:
            require(not (anchors.intersection(range(30, 38)) and anchors.intersection(range(40, 47))),
                    "ledger: правила разных версий представлены как один критический конфликт")


def main():
    parser = argparse.ArgumentParser(description="Офлайн-проверка материалов исполняемых примеров")
    parser.add_argument("--official", type=Path, default=ROOT / "submission/requirements.md")
    parser.add_argument("--runs", type=Path, default=HERE / "runs")
    parser.add_argument("--case", action="append", choices=CASES, dest="cases")
    args = parser.parse_args()
    for name, document in documents(args.official, args.cases or CASES).items():
        report, pairs, count = verify(name, args.runs / (name + ".json"), document)
        check_protocol(name, pairs)
        description = [(sorted(anchors), item["status"]) for anchors, item in pairs]
        print(f"{name}: {count} подтверждённых ссылок, {report['run']['fresh_calls']} вызовов, пары {description}")


if __name__ == "__main__":
    main()
