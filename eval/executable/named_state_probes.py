"""Vary the signing service's real return value without regenerating the review or its program."""

import argparse
from copy import deepcopy
import json
from pathlib import Path

from reviewer.executable import targets
from reviewer.witness_vm import simulate

from .audit import replay_upgrade, require
from .run import HERE


EXPECTED = {
    "сервис временно недоступен": {13: "AWAITING_SIGNATURE", 22: "MANUAL_REVIEW"},
    "подписано": {13: "READY", 22: "READY"},
    "сертификат отклонён": {13: "REJECTED", 22: "REJECTED"},
}
STATUSES = {"QUEUED", "AWAITING_SIGNATURE", "MANUAL_REVIEW", "READY", "REJECTED"}


def probe(report):
    lines = {ref["line"]: ref["text"] for ref in report["source"]}
    candidates = targets(report)
    item, = [item for item in report["executables"]["items"]
             if {candidates[item["finding_id"]][side]["source"]["line"] for side in ("first", "second")} == {13, 22}]
    require(item["status"] == "executed", "Обе строки найдены, но вычисление не подтверждено")
    program = item["program"]
    domains = {"enum:" + domain["key"]: {option["value"] for option in domain["options"]}
               for domain in program["domains"]}
    field, = [f["key"] for f in program["states"] if domains.get(f["unit"]) == STATUSES]
    input_key, = [f["key"] for f in program["inputs"] if domains.get(f["unit"]) == set(EXPECTED)]
    source_lines = {side: program[side]["source"]["line"] for side in ("first", "second")}
    output = []
    for response, expected in EXPECTED.items():
        case = deepcopy(program["scenario"])
        changed = [row for event in case["events"] for row in event["values"] if row["key"] == input_key]
        require(len(changed) == 1, "Ожидался один вход от сервиса подписи")
        changed[0]["value"] = response
        trace = simulate(program, lines, case, candidates[item["finding_id"]])
        require(trace["initial"][field] == "QUEUED", "Начальный статус должен быть QUEUED")
        final = {side: trace["final"][side][field] for side in source_lines}
        require(final == {side: expected[line] for side, line in source_lines.items()}, "Получен неверный статус GET")
        split = trace["first_difference"]
        require((split is not None) == (len(set(expected.values())) == 2), "Неверно определено расхождение")
        output.append({"service_response": response, "first_difference": split, "final": final})
    return {"input_sha256": report["run"]["input_sha256"], "finding_id": item["finding_id"],
            "source_lines": source_lines, "start": "QUEUED", "cases": output}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=HERE / "runs/named-state.json")
    parser.add_argument("--upgrade", action="store_true", help="Проверить дополнение к самому первому замечанию")
    args = parser.parse_args()
    if args.upgrade:
        report = replay_upgrade(HERE / "runs/before-named-state.json", HERE / "runs/named-state-upgrade.json")
    else:
        report = json.loads(args.report.read_bytes())
    print(json.dumps(probe(report), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
