"""Compare the frozen museum cases and BCC findings in actual full review reports."""

import argparse
import json
from pathlib import Path

from eval.score_review import score_report
from reviewer.executable import verify_source
from reviewer.full_render import validate_report


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent


def assess(report, fixture):
    document = ROOT / "submission/requirements.md" if fixture == "bcc" else FIXTURES / (fixture + ".md")
    verify_source(report, document)
    validate_report(report)
    findings = report["whole"]["findings"]
    critical = [f for f in findings if f["category"] == "нарушение" and f["priority"] == "критический"]
    if fixture == "bcc":
        checks = score_report(report, fixture)["checks"]
        pairs = {c["control"]: [f["id"] for f in c["findings"] if f in critical] for c in checks}
        passed = all(c["structural_check_passed"] for c in checks)
    else:
        pairs = {}
        expected = (("workorder", {4, 5}),) if fixture.startswith("workorder") else (
            ("workshop", {5, 15}), ("edition", {9, 16}))
        for label, lines in expected:
            matches = []
            for finding in critical:
                sources = {item["line"] for item in finding["evidence"]}
                witnesses = finding["triage"]["witness"]
                if (lines <= sources and finding["question"].strip().endswith("?") and
                    any({w[s]["source"]["line"] for s in ("first", "second")} == lines for w in witnesses)):
                    matches.append(finding["id"])
            pairs[label] = matches
        assigned = [fid for matches in pairs.values() for fid in matches]
        passed = (not critical if fixture.endswith("-aligned") else
                  len(critical) == len(expected) == len(set(assigned)) and all(len(ids) == 1 for ids in pairs.values()))
    result_questions = [f["id"] for f in findings if all(s in f["question"] for s in
                        ("result", "400", "401", "404", "500"))]
    return {"fixture": fixture, "model": report["run"]["model"], "checks_passed": passed,
            "findings_by_pair": pairs, "critical": [f["id"] for f in critical],
            "result_question": result_questions,
            "execution": {w["finding_id"]: w["status"] for w in report["executables"]["items"]},
            "fresh_calls": report["run"]["fresh_calls"], "restored_attempts": report["run"]["restored_attempts"],
            "elapsed_seconds": report["run"]["elapsed_seconds"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", choices=("bcc", "museum", "museum-aligned", "workorder", "workorder-aligned"))
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    result = assess(json.loads(args.report.read_bytes()), args.fixture)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["checks_passed"] else 1)
