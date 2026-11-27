"""Verify input bytes, six operation-scoped examples and the full archived model runs."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from eval.executable.audit import require, verify

INPUTS = {
    "planetarium": "44e6c8a3b50c68361093d6595670b306930254ea297897cdaca9695e63bd821f",
    "planetarium-aligned": "dd7c19cbfd5746fe5591d27406887bfeb5f04807c429337ba2a9a3b36002ff2a",
}
EXAMPLES = {
    22: ("POST /scenes/{scene}/proofs", 202, ("nextStep", "proofRef")),
    28: ("POST /scenes/{scene}/proofs", 409, ("hint",)),
    34: ("POST /scenes/{scene}/proofs", 422, ("nextStep", "hint")),
    40: ("POST /scenes/{scene}/proofs", 503, ("hint", "cooldownSec", "nextStep")),
    52: ("GET /scenes/{scene}/screen-check", 200, ("nextStep", "domeLabel")),
    58: ("GET /scenes/{scene}/screen-check", 409, ("nextStep", "deskNote")),
}
POST = "POST /scenes/{scene}/proofs"
GET = "GET /scenes/{scene}/screen-check"


def obligations(report, aligned):
    expected = {
        (POST, "nextStep"): {409: "excluded" if aligned else "unspecified",
                              422: "required", 503: "required" if aligned else "unspecified"},
        (POST, "proofRef"): {409: "excluded", 422: "excluded", 503: "excluded"},
        (POST, "hint"): {409: "required", 422: "required", 503: "required"},
        (POST, "cooldownSec"): {409: "excluded", 422: "excluded", 503: "required"},
        (GET, "nextStep"): {409: "required"},
        (GET, "domeLabel"): {409: "excluded"},
        (GET, "deskNote"): {409: "required"},
    }
    section = report.get("response_field_review")
    if not section:
        return None
    candidates = {c["id"]: c for c in section["candidates"]}
    policies, questions, crossed = {}, [], []
    for rule in section["assessments"]:
        target = candidates[rule["candidate"]]
        method = {5: POST, 43: GET}.get(target["operation"], "unknown operation")
        field = target["field"]
        if rule["questions"]:
            questions.append({"operation": method, "field": field, "questions": rule["questions"]})
        for ruling in rule["rulings"]:
            for start in ruling["examples"]:
                raw_method, status, _ = EXAMPLES[start + 1]
                policies[method, field, status] = ruling["policy"]
                if raw_method != method:
                    crossed.append({"operation": method, "field": field, "example": start})
            for ref in ruling["evidence"]:
                if (method == POST and ref["line"] >= 43) or (method == GET and 5 <= ref["line"] < 43):
                    crossed.append({"operation": method, "field": field, "evidence": ref})
    rows = [{"operation": method, "field": field, "status": code, "expected": policy,
             "actual": policies.get((method, field, code))}
            for (method, field), outcomes in expected.items() for code, policy in outcomes.items()]
    return {"rows": rows, "correct": sum(row["expected"] == row["actual"] for row in rows),
            "observed_entries": len(policies), "cross_operation": crossed, "questions": questions}


def final_questions(report):
    matches = {}
    for code, line in ((409, 28), (503, 40)):
        matched = []
        for finding in report["whole"]["findings"]:
            question = finding["question"]
            if (re.search(r"(?<!\w)nextStep(?!\w)", question)
                    and re.search(rf"(?<!\d){code}(?!\d)", question)
                    and any(5 <= ref["line"] < 43 for ref in finding["evidence"])):
                matched.append({"id": finding["id"], "question": question,
                                "cites_response_body": any(ref["line"] == line for ref in finding["evidence"])})
        matches[code] = matched
    return matches


def inspect(folder, name):
    source = HERE / (name + ".md")
    require(sha256(source.read_bytes()).hexdigest() == INPUTS[name], f"Source changed: {source}")
    path = folder / (name + ".json")
    report, pairs, citations = verify(name, path, source)
    lines = source.read_text().splitlines()
    shapes = report["example_shapes"]
    require(len(shapes) == len(EXAMPLES), f"{name}: missing response examples")
    saved = {}
    for shape in shapes:
        line = shape["start"] + 1
        operation, status, fields = EXAMPLES[line]
        raw_keys = list(json.loads(lines[line - 1]))
        require(raw_keys == list(fields) == shape["keys"] and shape["key_count"] == len(fields),
                f"{name} L{line}: response keys")
        require(operation in shape["section"] and "HTTP " + str(status) in shape["section"],
                f"{name} L{line}: wrong operation or status")
        saved[line] = [operation, status, raw_keys]
    require(set(saved) == set(EXAMPLES), f"{name}: response identity")
    result = {"sha256": sha256(path.read_bytes()).hexdigest(), "commit": report["run"]["agent_version"],
              "duration_s": report["run"]["elapsed_seconds"], "fresh_calls": report["run"]["fresh_calls"],
              "citations": citations, "examples": saved, "executables": [(sorted(a), i["status"]) for a, i in pairs],
              "policies": obligations(report, name.endswith("-aligned")), "target_questions": final_questions(report)}
    return result, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cohorts", nargs="*", default=["before", "release"], help="Run folders under runs/")
    args = parser.parse_args()
    output = {}
    for name in args.cohorts:
        rows = {}
        for case in INPUTS:
            rows[case], _ = inspect(HERE / "runs" / name, case)
        require(rows["planetarium"]["examples"] == rows["planetarium-aligned"]["examples"],
                f"{name}: document variants changed their examples")
        output[name] = rows
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
