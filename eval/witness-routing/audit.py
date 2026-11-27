"""Replay recorded proposals on their exact witness and check the preregistered observable."""

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from eval.executable.audit import require
from eval.executable.named_state_probes import probe as named_statuses
from eval.executable.sdk_oracle import verify as verify_sdk_ledger
from reviewer import witness_vm
from reviewer.executable import VERSION, check_audit, targets
from reviewer.full_render import validate_report

HERE = Path(__file__).resolve().parent
PRIMARY = ("month", "push", "hotel", "fee", "status")
ANCHORS = {"month": {38, 64}, "push": {39, 40}, "hotel": {12, 16}, "fee": {9, 13},
           "status": {13, 22}, "sdk": {44, 66}}


def verify(path):
    row = json.loads(path.read_bytes())
    tag, item = row["document"], row["item"]
    raw = (ROOT / row["baseline"]).read_bytes()
    require(sha256(raw).hexdigest() == row["baseline_sha256"], f"{path}: baseline")
    report = json.loads(raw)
    lines = validate_report(report)
    witness = targets(report, VERSION)[row["finding_id"]]
    require({witness[s]["source"]["line"] for s in ("first", "second")} == ANCHORS[tag], f"{path}: исходные правила")
    require(row["auditor"] == "gpt-6.2-astra" and row["restored_attempts"] == 0, f"{path}: путь или checkpoint")
    model = path.stem.rsplit("-", 1)[-1]
    require(row["proposer"] == "gpt-6.2-" + model and path.stem == tag + "-" + model, f"{path}: неверный маршрут")
    info = {"utc_end": row["utc_end"], "seconds": row["seconds"], "calls": row["fresh_calls"]}
    if item["status"] != "executed":
        failed = any(a["status"] == "failed" for a in row["attempts"])
        return {**info, "outcome": "provider_failure" if failed else "abstained", "reason": item["reason"]}
    program = item["program"]
    computed = witness_vm.execute(program, lines, witness)
    require(json.dumps(computed, sort_keys=True) == json.dumps(item["result"], sort_keys=True), f"{path}: трасса")
    check_audit(item["audit"], witness, lines)
    require(item["audit"]["verdict"] == "accepted" and computed["control"]["first_difference"] is None, f"{path}: контроль")
    split = computed["scenario"]["first_difference"]
    field, = [f for f in program["states"] if f["key"] == split["field"]]
    label, unit = field["label"].lower(), field["unit"]
    values = (split["first"], split["second"])
    details = {"observable": field, "difference": split, "control_final": computed["control"]["final"]}
    if tag == "month":
        multiplier = {"₸": 1, "тенге": 1, "тиын": 100}.get(unit)
        require(multiplier and "феврал" in label and values == (0, 4000 * multiplier), f"{path}: месяц/единица")
        require(computed["control"]["final"]["first"][field["key"]] == 4000 * multiplier, f"{path}: контроль февраля")
    elif tag == "push":
        require(any(w in label for w in ("push", "уведомлен")) and values == (1, 2), f"{path}: push")
        require(computed["control"]["final"]["first"][field["key"]] == 1, f"{path}: контроль push")
    elif tag == "hotel":
        balance = "доступн" in label and values == (43300, 54500)
        held = "заблокир" in label and values == (11200, 0)
        require((balance or held) and unit in ("₸", "тенге"), f"{path}: остаток/блокировка")
    elif tag == "fee":
        require("выписк" in label and "комисси" in label and unit == "тиын" and split["step"] == 3
                and values == (9, 10), f"{path}: комиссия в выписке")
    elif tag == "status":
        revision = {**report, "executables": {"version": VERSION, "items": [item]}}
        details["all_signing_responses"] = named_statuses(revision)["cases"]
    else:
        oracle = json.loads((ROOT / "eval/executable/runs/sdk-ledger-oracle.json").read_bytes())["cases"]
        verify_sdk_ledger(oracle)
        measured = tuple(oracle[name]["events"][0]["balances"]["101"]["debits_pending"]
                         for name in ("sdk-original", "sdk-fixed"))
        require("101" in label and "ожида" in label and split["step"] == 1 and values == measured, f"{path}: SDK")
        return {**info, **details, "outcome": "sdk_projection"}
    return {**info, **details, "outcome": "passed"}


def audit(folder):
    rows = {path.stem: verify(path) for path in sorted(folder.glob("*.json"))}
    require(set(rows) == {f"{tag}-{m}" for tag in (*PRIMARY, "sdk") for m in ("astra", "sol")}, "Неполный архив")
    ratios = {}
    for tag in (*PRIMARY, "sdk"):
        a, s = rows[tag + "-astra"], rows[tag + "-sol"]
        if a["outcome"] == s["outcome"] and a["outcome"] in ("passed", "sdk_projection"):
            ratios[tag] = s["seconds"] / a["seconds"]
    five = [ratios[tag] for tag in PRIMARY if tag in ratios]
    return {"round": folder.name, "ended_utc": max(datetime.fromisoformat(r["utc_end"]) for r in rows.values()).isoformat(),
            "rows": rows, "sol_over_astra": ratios,
            "median_five": median(five) if len(five) == len(PRIMARY) else None,
            "median_six": median(ratios.values()) if len(ratios) == 6 else None}


if __name__ == "__main__":
    print(json.dumps([audit(folder) for folder in sorted((HERE / "runs").iterdir())], ensure_ascii=False, indent=2))
