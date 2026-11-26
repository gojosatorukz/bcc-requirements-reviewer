"""Compare the generated hotel program with the preregistered arithmetic oracle on new inputs."""

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from random import Random

from reviewer.executable import targets
from reviewer.witness_vm import simulate

from .audit import require, verify
from .oracle import reference
from .run import HERE


def inputs_for(program, book, deposit, charge):
    case = deepcopy(program["scenario"])
    initial = {"balance": book, "available": book, "held": 0}
    for field in case["initial"]:
        field["value"] = initial[field["key"]]
    values = {"deposit": deposit, "charge": charge}
    for event in case["events"]:
        for field in event["values"]:
            field["value"] = values[field["key"]]
    return case


def probe(report_path):
    report, pairs, _ = verify("hotel", report_path, HERE / "card-capture.md")
    ((_, item),) = pairs
    fid = item["finding_id"]
    witness = targets(report)[fid]
    require(witness["first"]["source"]["line"] == 12 and witness["second"]["source"]["line"] == 16,
            "Для сохранённой программы ожидаются К-2, затем В-1")
    program = item["program"]
    require(program["observe"] == ["available"], "Сохранённый отчёт показывает гостю доступный остаток")
    # Expose the same program's other two state variables solely to check its accounting.
    observable = dict(program, observe=["available", "balance", "held"])
    source = {s["line"]: s["text"] for s in report["source"]}
    examples = [(71_900, 28_600, c) for c in (17_400, 28_599, 28_600)]
    small = [(book, deposit, charge) for book in range(1, 21)
             for deposit in range(1, book + 1) for charge in range(1, deposit + 1)]
    rng = Random(20261127)
    large = []
    for _ in range(256):
        book = rng.randint(100, 1_000_000_000)
        deposit = rng.randint(1, book)
        large.append((book, deposit, rng.randint(1, deposit)))
    results = []
    equal = 0
    for book, deposit, charge in [*examples, *small, *large]:
        case = inputs_for(program, book, deposit, charge)
        computed = simulate(observable, source, case, witness)
        oracle = reference(book, deposit, charge)
        for step, (first, second) in zip(computed["steps"], zip(oracle[0][1:], oracle[1][1:]), strict=True):
            for name, expected in (("first", first), ("second", second)):
                fields = {"available": expected.available, "balance": expected.book, "held": expected.hold}
                require(step[name] == fields, f"Расхождение c независимым расчётом: {book} / {deposit} / {charge}")
        diff = computed["first_difference"]
        if charge == deposit:
            require(diff is None, "Полная сумма клиринга не должна расходиться")
            equal += 1
        else:
            require(diff and diff["step"] == 2 and diff["field"] == "available", "Неверная точка расхождения")
        if (book, deposit, charge) in examples:
            results.append({"book": book, "authorization": deposit, "capture": charge,
                            "first_difference": diff, "final": computed["final"]})
    return {"report_sha256": sha256(report_path.read_bytes()).hexdigest(), "preregistered_examples": results,
            "all_valid_triples_up_to_balance_20": len(small), "seeded_cases": len(large), "seed": 20261127,
            "total": len(examples) + len(small) + len(large), "cases_with_equal_final_results": equal}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Офлайн-проверка программы, построенной для карты гостиницы")
    parser.add_argument("--report", type=Path, default=HERE / "runs/hotel.json")
    args = parser.parse_args()
    print(json.dumps(probe(args.report), ensure_ascii=False, indent=2))
