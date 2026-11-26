"""Verify every pair of rounding residues on the agent's saved merchant-fee program."""

import argparse
from copy import deepcopy
from decimal import Decimal, ROUND_FLOOR
from hashlib import sha256
from itertools import product
import json
from pathlib import Path

from reviewer.executable import targets
from reviewer.witness_vm import simulate

from .audit import require, verify
from .run import HERE


def rounded(amount):
    return int((Decimal(amount) * Decimal("0.0125")).to_integral_value(rounding=ROUND_FLOOR))


def probe(report_path):
    report, pairs, _ = verify("fee", report_path, HERE / "minor-units/merchant-fee.md")
    ((_, item),) = pairs
    p = item["program"]
    witness = targets(report)[item["finding_id"]]
    source = {s["line"]: s["text"] for s in report["source"]}
    require([p[k]["source"]["line"] for k in ("first", "second")] == [9, 13], "Неверная пара правил")
    examples = [(399, 401), (399, 400), (400, 400)]
    boundaries = list(product(range(1, 81), repeat=2))
    examples_out = []
    differences = 0
    for first, second in [*examples, *boundaries]:
        case = deepcopy(p["scenario"])
        for event, amount in zip(case["events"][:2], [first, second], strict=True):
            require(event["kind"] == "payment" and event["values"][0]["key"] == "amount", "Неверное событие оплаты")
            event["values"][0]["value"] = amount
        trace = simulate(p, source, case, witness)
        per_purchase = rounded(first) + rounded(second)
        daily = rounded(first + second)
        require(trace["final"] == {"first": {"fee": per_purchase}, "second": {"fee": daily}}, "Неверная комиссия")
        require(all(step[k]["fee"] == 0 for step in trace["steps"][:2] for k in ("first", "second")),
                "Комиссия показана до закрытия дня")
        diff = trace["first_difference"]
        require((diff is None) == (per_purchase == daily), "Неверно определено расхождение")
        if diff:
            require(diff["step"] == 3 and diff["field"] == "fee", "Первое расхождение должно быть в выписке")
            differences += 1
        if (first, second) in examples:
            examples_out.append({"payments_tiyin": [first, second], "round_each": per_purchase,
                                 "round_daily_total": daily, "first_difference": diff})
    return {"report_sha256": sha256(report_path.read_bytes()).hexdigest(), "preregistered_examples": examples_out,
            "positive_pairs_covering_all_remainders_modulo_80": len(boundaries),
            "total": len(examples) + len(boundaries), "different_at_day_close": differences}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Офлайн-проверка округлений для двух оплат")
    parser.add_argument("--report", type=Path, default=HERE / "runs/fee.json")
    args = parser.parse_args()
    print(json.dumps(probe(args.report), ensure_ascii=False, indent=2))
