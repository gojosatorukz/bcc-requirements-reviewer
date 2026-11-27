"""Score a frozen acceptance case from the JSON actually produced by the application."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re

from reviewer.executable import verify_source
from reviewer.full_render import validate_report


HASHES = {
    "ledger-a": "be906d81f8c0b08d43d5617c2180a784925ac0c8bd106270f69116e9fdd7a482",
    "ledger-b": "271a175221ff98fa606c5404d6ad345536c4325a24cedcb4e9c2f421c0abf863",
    "ledger-c": "756988df182de81e0bf37c48ba44728273093d38a193fae8ae1097d03a44d541",
    "recorder-v1": "bc4ea66e75d17f207567498d214cf1cb12434e82f36872ad7029ae5aae20c98d",
    "recorder-v2": "6cb53a112325a7649fd58470e241c9438d61cf0d8f57da4c8648fa93f1192ba9",
}
# One explicit rule, its acceptance line, the calculated result, the expected result.
DISPUTES = {"ledger-b": (15, 19, 500, 100), "recorder-v1": (7, 11, 33, 37)}


def score(source, file):
    case = source.stem
    if sha256(source.read_bytes()).hexdigest() != HASHES[case]:
        raise ValueError("Input differs from the frozen case")
    raw = file.read_bytes()
    report = json.loads(raw)
    validate_report(report)
    verify_source(report, source)
    findings = report["whole"]["findings"]
    violations = [f for f in findings if f["category"] == "нарушение"]
    run = report["run"]
    result = {"case": case, "input_sha256": HASHES[case], "report_sha256": sha256(raw).hexdigest(),
              "model": run["model"], "agent_version": run["agent_version"], "engine_sha256": run["engine_sha256"],
              "seconds": run["elapsed_seconds"], "fresh_calls": run["fresh_calls"],
              "restored_attempts": run["restored_attempts"], "violations": len(violations),
              "open_questions": len(findings) - len(violations)}
    if case not in DISPUTES:
        return {**result, "agrees_with_control": not violations}
    rule, expected_line, computed, expected = DISPUTES[case]
    matches = []
    for f in violations:
        cited = {e["line"] for e in f["evidence"]}
        observed = [f["title"], f["question"], f.get("reason", "")]
        witness = f.get("triage", {}).get("witness", [])
        for w in witness:
            observed.extend([w["first"]["outcome"], w["second"]["outcome"]])
        numbers = set(re.findall(r"(?<!\d)\d+(?!\d)", " ".join(observed)))
        if {rule, expected_line} <= cited and {str(computed), str(expected)} <= numbers:
            matches.append(f)
    examples = {i["finding_id"]: i for i in report.get("executables", {}).get("items", [])}
    matched = []
    for f in matches:
        item = examples.get(f["id"], {})
        row = {"id": f["id"], "question": f["question"],
               "two_rule_status": item.get("status"), "fixed_input_matches": False}
        if "acceptance_example" in item:
            trace = item["acceptance_example"]["result"]
            row.update(computed=trace["computed"], expected=trace["expected"], unit=trace["unit"],
                       expected_source_line=trace["expected_source"]["line"],
                       steps=[{"line": s["source"]["line"], "before": s["before"], "after": s["after"]}
                              for s in trace["steps"]])
            row["fixed_input_matches"] = (trace["computed"] == computed and trace["expected"] == expected
                                          and trace["expected_source"]["line"] == expected_line)
        matched.append(row)
    return {**result, "matched": matched, "unmatched_violations": len(violations) - len(matches)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    print(json.dumps(score(args.source, args.report), ensure_ascii=False, indent=2))
