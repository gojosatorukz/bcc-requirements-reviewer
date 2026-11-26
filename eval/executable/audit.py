"""Verify actual CLI artifacts, ground citations and recompute traces from saved programs."""

import argparse
from hashlib import sha256
import json
from pathlib import Path

from reviewer.executable import targets, validate_saved
from reviewer.full_render import validate_report

from .run import CASES, HERE, MODEL, ROOT, documents
from .sdk_oracle import verify as verify_ledger


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


def check_protocol(name, report, pairs):
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
    if name in ("sdk-original", "sdk-fixed"):
        target = []
        for finding in report["whole"]["findings"]:
            rows = {ref["line"] for ref in finding["evidence"]}
            if (finding["category"] == "нарушение" and finding["candidates"] and 66 in rows
                    and rows.intersection(range(74, 78))):
                target.append(finding["id"])
        require(len(target) == int(name == "sdk-original"),
                f"{name}: найдено замечаний об исходном флаге {len(target)}")
        if name == "sdk-original" and report["executables"]["version"] != "witness-v1":
            item, = [item for _, item in pairs if item["finding_id"] in target]
            if item["status"] == "executed":
                split = item["result"]["scenario"]["first_difference"]
                require(split["step"] == 1 and {split["first"], split["second"]} == {0, 10},
                        "sdk-original: результат расходится с состояниями реального реестра")
            else:
                require(item["status"] == "unsupported" and item["reason"].strip(),
                        "sdk-original: причина отказа от исполнения отсутствует")


def compare_sdk(runs):
    original = runs / "before-sdk-original.json"
    source = documents(ROOT, ("sdk-original", "sdk-fixed"))
    before, pairs, _ = verify("sdk-original", original, source["sdk-original"])
    check_protocol("sdk-original", before, pairs)
    fixed, pairs, _ = verify("sdk-fixed", runs / "before-sdk-fixed.json", source["sdk-fixed"])
    check_protocol("sdk-fixed", fixed, pairs)
    patch = json.loads((runs / "sdk-upgrade.json").read_bytes())
    require(set(patch) == {"execution_run", "executables"}, "sdk-upgrade: лишние поля в дополнении")
    execution = patch["execution_run"]
    require(execution["base_report_sha256"] == sha256(original.read_bytes()).hexdigest()
            and execution["fresh_calls"] > 0 and execution["restored_attempts"] == 0,
            "sdk-upgrade: дополнение не связано с исходным холодным прогоном")
    updated = {**before, **patch}
    validate_saved(updated, validate_report(updated))
    cases = json.loads((runs / "sdk-ledger-oracle.json").read_bytes())["cases"]
    verify_ledger(cases)
    item, = patch["executables"]["items"]
    split = item["result"]["scenario"]["first_difference"]
    field = split["field"]
    observed = [case["events"][0]["balances"]["101"]["debits_pending"] for case in cases.values()]
    require(split["step"] == 1 and sorted((split["first"], split["second"])) == sorted(observed),
            "sdk-upgrade: вычисления расходятся с независимой БД")
    control = item["result"]["control"]
    for step, external in zip(control["steps"], cases["sdk-fixed"]["events"], strict=True):
        expected = external["balances"]["101"]["debits_pending"]
        require(step["first"][field] == step["second"][field] == expected,
                "sdk-upgrade: контроль расходится с независимой БД")
    print("sdk-upgrade: привязка к исходному ревью, офлайн-исполнение и внешние остатки подтверждены")


def main():
    parser = argparse.ArgumentParser(description="Офлайн-проверка материалов исполняемых примеров")
    parser.add_argument("--official", type=Path, default=ROOT / "submission/requirements.md")
    parser.add_argument("--runs", type=Path, default=HERE / "runs")
    parser.add_argument("--case", action="append", choices=CASES, dest="cases")
    parser.add_argument("--sdk-history", action="store_true", help="Сверить первый прогон и его дополнение с независимой БД")
    args = parser.parse_args()
    for name, document in documents(args.official, args.cases or CASES).items():
        report, pairs, count = verify(name, args.runs / (name + ".json"), document)
        check_protocol(name, report, pairs)
        description = [(sorted(anchors), item["status"]) for anchors, item in pairs]
        print(f"{name}: {count} подтверждённых ссылок, {report['run']['fresh_calls']} вызовов, пары {description}")
    if args.sdk_history:
        compare_sdk(args.runs)


if __name__ == "__main__":
    main()
