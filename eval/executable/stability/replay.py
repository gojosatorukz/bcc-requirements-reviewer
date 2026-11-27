"""Verify the archived cold runs and put independent hotel scenarios on identical inputs."""

from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from eval.executable.audit import check_protocol, require, verify
from eval.executable.named_state_probes import probe as named_statuses
from eval.executable.oracle import reference
from eval.executable.run import documents
from reviewer.executable import targets
from reviewer.witness_vm import simulate

HERE = Path(__file__).resolve().parent
CASES = ("official", "hotel", "aligned", "ledger", "named-state")


def fixed_hotel(report, item, source):
    program = item["program"]
    witness = targets(report)[item["finding_id"]]
    result = []
    for charge in (17_400, 28_599, 28_600):
        sample = deepcopy(program["scenario"])
        start = {"balance": 71900, "ledger": 71900, "held": 0, "available": 71900}
        for row in sample["initial"]:
            row["value"] = start[row["key"]]
        for event in sample["events"]:
            for row in event["values"]:
                row["value"] = {"deposit": 28600, "charge": charge}[row["key"]]
        trace = simulate(program, source, sample, witness)
        first, second = reference(71900, 28600, charge)
        for side, states in (("first", first), ("second", second)):
            for step, state in zip(trace["steps"], states[1:], strict=True):
                expected = {"balance": state.book, "ledger": state.book, "held": state.hold, "available": state.available}
                require(all(v == expected[k] for k, v in step[side].items()), "hotel: согласование с эталонной книгой")
        require((trace["first_difference"] is None) == (charge == 28600), "hotel: неверное наличие расхождения")
        result.append({"charge": charge, "first_difference": trace["first_difference"], "final": trace["final"]})
    return result


def check(path, name, document):
    review, pairs, references = verify(name, path, document)
    check_protocol(name, review, pairs)
    source = {row["line"]: row["text"] for row in review["source"]}
    run = review["run"]
    findings = review["whole"]["findings"]
    data = {key: run[key] for key in ("agent_version", "started_utc", "elapsed_seconds", "fresh_calls", "input_sha256")}
    data.update(references=references, candidates=len(review["conflicts"]),
                priorities=Counter(f["priority"] for f in findings), pairs=[])
    for anchors, item in pairs:
        outcome = {"id": item["finding_id"], "sources": sorted(anchors), "status": item["status"]}
        if item["status"] == "executed":
            program, result = item["program"], item["result"]
            split = result["scenario"]["first_difference"]
            field, = [f for f in program["states"] if f["key"] == split["field"]]
            outcome.update(observable=field, difference=split, final=result["scenario"]["final"],
                           control_final=result["control"]["final"])
            if name == "hotel":
                outcome["same_hotel_inputs"] = fixed_hotel(review, item, source)
            if name == "official" and anchors == {38, 64}:
                feb, = [f for f in program["states"] if f["key"] in program["observe"] and "феврал" in f["label"].lower()]
                factor = {"₸": 1, "тенге": 1, "тиын": 100}[feb["unit"]]
                final = result["scenario"]["final"]
                require((final["first"][feb["key"]], final["second"][feb["key"]]) == (0, 4000 * factor), "bcc: февраль")
            if name == "official" and anchors == {39, 40}:
                require((split["first"], split["second"]) == (1, 2) and
                        any(w in field["label"].lower() for w in ("push", "уведомлен")), "bcc: push")
        else:
            outcome["reason"] = item["reason"]
        data["pairs"].append(outcome)
    if name == "named-state":
        data["all_signing_responses"] = named_statuses(review)["cases"]
    return data


if __name__ == "__main__":
    official = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "submission/requirements.md"
    sources = documents(official, CASES)
    manifest = {entry["report"]: entry for entry in json.loads((HERE / "runs.json").read_bytes())}
    require(len(manifest) == 7, "В журнале должны быть две ночные и пять утренних записей")
    checks = {}
    for cohort, location in (("baseline", HERE.parent / "runs"), ("initial", HERE / "initial"), ("recovered", HERE / "recovered")):
        names = ("official", "hotel") if cohort == "initial" else CASES
        checks[cohort] = {name: check(location / (name + ".json"), name, sources[name]) for name in names}
        if cohort != "baseline":
            for name, inspected in checks[cohort].items():
                record = manifest[f"{cohort}/{name}.json"]
                raw = (location / (name + ".json")).read_bytes()
                require(sha256(raw).hexdigest() == record["sha256"] and record["exit_code"] == 0
                        and record["commit"] == inspected["agent_version"]
                        and record["input_sha256"] == inspected["input_sha256"], f"Расхождение с журналом: {name}")
    print(json.dumps(checks, ensure_ascii=False, indent=2))
