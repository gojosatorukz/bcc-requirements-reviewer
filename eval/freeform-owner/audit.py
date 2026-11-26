"""Verify source identity, verbatim citations, and preregistered decision outcomes."""

import argparse
import json
from hashlib import sha256
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASE = ROOT / "runs/bcc-submission/review.json"
BASE_SHA = "9938b9c13e4bdde61157a049b5ac69985fbbfe328474c41ffbbd96003fb53706"
ORIGINAL_SHA = "e7dae55e5453a250d93e335f5ff9c7f775f4457ccddd333f4a67e47b20d74381"
MODEL = "gpt-6.2-astra"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(path.read_bytes())


def audit(runs, original):
    require(sha256(BASE.read_bytes()).hexdigest() == BASE_SHA, "Baseline report hash mismatch")
    require(sha256(original.read_bytes()).hexdigest() == ORIGINAL_SHA, "Official input hash mismatch")
    partial = read(runs / "turn-1.json")
    ready = read(runs / "turn-2.json")
    for turn in (partial, ready):
        require(turn["finding_id"] == "D-01" and turn["baseline_sha256"] == BASE_SHA, "Wrong binding")
        require(turn["finding_status"] == "открыто", "Dialogue alone closed the finding")
        run = turn["run"]
        require(run["provider"] == "codex-cli" and run["model"] == MODEL and run["fresh_calls"] > 0
                and run["restored_attempts"] == 0, "Expected a fresh dialogue run")
        lines = original.read_text(encoding="utf-8").splitlines()
        for previous_turn in turn["turns"]:
            for item in previous_turn["evidence"]:
                require(0 < item["line"] <= len(lines) and item["quote"] in lines[item["line"] - 1],
                        "Invalid original source quotation")
            for obligation in previous_turn["commitments"]:
                for item in obligation["support"]:
                    require(0 < item["turn"] <= len(turn["answers"]) and item["quote"]
                            and item["quote"] in turn["answers"][item["turn"] - 1], "Unsupported owner commitment")
    require(partial["state"] == "needs_followup" and not partial["rule"], "Partial answer became a rule")
    require(partial["follow_up"].count("?") == 1, "Expected one follow-up question")
    require(ready["state"] == "ready" and ready["kind"] == "third", "Third option lost")
    require(not ready["follow_up"] and ready["rule"], "Missing completed rule")
    require(ready["answers"][:-1] == partial["answers"], "Lost previous answer")
    require(ready["turns"][0] == partial["turns"][0] and
            ready["turns"][1]["question"] == partial["follow_up"], "Question was rewritten between turns")
    complete = HERE / "complete.md"
    february = HERE.parent / "owner-decision/february.md"
    cases = [
        ("partial-complete", partial, complete, "устранено", "не подтверждено", "открыто"),
        ("ready-original", ready, original, "сохранилось", "не подтверждено", "открыто"),
        ("ready-february", ready, february, "устранено", "противоречит решению", "открыто"),
        ("ready-complete", ready, complete, "устранено", "выполнено", "закрыто"),
        ("ready-wrong-same-month", ready, HERE / "wrong-same-month.md", "устранено", "противоречит решению", "открыто"),
    ]
    for name, turn, source, technical, agreement, workflow in cases:
        report = read(runs / f"{name}.json")
        run = report["run"]
        row, = report["checks"]
        clarification = row["clarification"]
        require(run["input_sha256"] == sha256(source.read_bytes()).hexdigest(), f"{name}: wrong input")
        require(run["previous_report_sha256"] == BASE_SHA and row["id"] == "D-01", f"{name}: wrong task")
        require(clarification["transcript"] == turn, f"{name}: different transcript")
        observed = (row["status"], clarification["status"], row["workflow_status"])
        require(observed == (technical, agreement, workflow), f"{name}: unexpected {observed}")
        lines = source.read_text(encoding="utf-8").splitlines()
        require(report["source"] == [{"line": n, "text": s} for n, s in enumerate(lines, 1) if s.strip()],
                f"{name}: report snapshot diverges from source")
        citations = list(row["revised_evidence"])
        for condition in clarification["conditions"]:
            citations.extend(condition["evidence"])
            if condition["status"] != "не подтверждено":
                require(condition["evidence"], f"{name}: no evidence for condition")
        for cite in citations:
            index = cite["line"] - 1
            require(0 <= index < len(lines) and cite["quote"] and cite["quote"] in lines[index],
                    f"{name}: bad quotation at line {index + 1}")
        if technical == "устранено":
            changed = {entry["line"] for hunk in report["diff"] if hunk["id"] in row["diff_hunks"]
                       for entry in hunk["after"]}
            require(changed & {item["line"] for item in row["revised_evidence"]},
                    f"{name}: no revised evidence at the change")
        if name == "ready-original":
            require(run["fresh_calls"] == 0 and not run["attempts"], f"{name}: unexpected model call")
        else:
            require(run["fresh_calls"] >= 1 and run["provider"] == "codex-cli" and run["model"] == MODEL,
                    f"{name}: missing real call")
            require(run["restored_attempts"] == 0, f"{name}: reused answer")
        print(f'{name}: {", ".join(observed)} | {len(citations)} цитат | {run["fresh_calls"]} вызовов')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, default=HERE / "runs")
    parser.add_argument("--original", type=Path, default=ROOT / "submission/requirements.md")
    args = parser.parse_args()
    audit(args.runs, args.original)
