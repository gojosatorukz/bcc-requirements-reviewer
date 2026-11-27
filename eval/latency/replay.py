"""Independently inspect raw full-run timing and semantic evidence, without using a model."""

import argparse
from datetime import datetime
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from eval.executable.audit import check_protocol, require, source_citations, verify
from reviewer.executable import targets
from reviewer.full_render import validate_report

FIELD = ROOT / "eval/field-obligations"
spec = importlib.util.spec_from_file_location("planetarium_audit", FIELD / "replay.py")
fields = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fields)

BASELINE = "4845a4d"
OLD_OFFICIAL = "460c1efdc009a25f22d4f14f331a3734622b71678099def151e0cb0e076fae2b"
BCC_INPUT = "e7dae55e5453a250d93e335f5ff9c7f775f4457ccddd333f4a67e47b20d74381"
STATUS_CODES = (400, 401, 404, 500)
QA_EXPECTED = {"initial_action": "needs_decision", "retry_effect": "needs_decision",
               "reentry": "specified", "boundary": "specified", "isolation": "specified"}


def timing(report):
    run = report["run"]
    calls = [a for a in run["attempts"] if not a.get("restored")]
    require(len(calls) == run["fresh_calls"], "Inconsistent call count")
    times = {a["stage"]: 0.0 for a in calls}
    for a in calls:
        times[a["stage"]] += a["seconds"]

    result = {"commit": run["agent_version"], "started_utc": run["started_utc"],
              "elapsed_s": run["elapsed_seconds"], "workers": run["max_workers"],
              "fresh": len(calls), "restored": run["restored_attempts"], "usage": run["current_usage"],
              "rejections": [{"stage": a["stage"], "status": a["status"], "reason": a.get("reason")}
                             for a in calls if a["status"] != "accepted"],
              "sum_by_stage_s": {name: round(t, 2) for name, t in times.items()}}
    if all("started_utc" in a for a in calls):
        intervals = []
        zero = datetime.fromisoformat(run["started_utc"]).timestamp()
        for a in calls:
            begin = datetime.fromisoformat(a["started_utc"]).timestamp() - zero
            intervals.append((begin, begin + a["seconds"], a["stage"]))
        result["intervals_s"] = [{"stage": stage, "start": round(start, 2), "end": round(end, 2)}
                                 for start, end, stage in sorted(intervals)]
    else:
        slots = [0.0] * run["max_workers"]
        for stage, seconds in times.items():
            if stage == "Сценарии" or stage.startswith(("Блоки ", "Поля ответов ")):
                slot = slots.index(min(slots))
                slots[slot] += seconds
        tails = {}
        for stage, seconds in times.items():
            match = re.search(r" D-\d+$", stage)
            if match:
                key = match.group().strip()
                tails[key] = tails.get(key, 0.0) + seconds
        # All three reference jobs had fewer witness branches than available workers.
        require(len(tails) <= len(slots), "Reconstruct final-phase dispatch for this run")
        result["reconstructed_phases_s"] = {
            "local_fanout": round(max(slots), 2), "whole": times["Весь документ"],
            "qa": times["Сценарии QA"], "witnesses": round(max(tails.values(), default=0), 2)}
    return result


def verify_artifact(name, path, source, allow_restored):
    report = json.loads(path.read_bytes())
    run = report["run"]
    if run["restored_attempts"] == 0:
        return verify(name, path, source)
    require(allow_restored, "Resumed artifact; use --allow-restored for semantic evidence only")
    require(report["schema_version"] == 2 and run["model"] == "gpt-6.2-astra"
            and run["provider"] == "codex-cli" and "execution_run" not in report,
            "Full Astra report required")
    require(run["input_sha256"] == sha256(source.read_bytes()).hexdigest(), "Recovered input mismatch")
    raw = {n: l for n, l in enumerate(source.read_text().splitlines(), 1) if l.strip()}
    require(validate_report(report) == raw, "Recovered source snapshot mismatch")
    citations = list(source_citations(report))
    require(all(ref["quote"] and ref["quote"] in raw.get(ref["line"], "") for ref in citations),
            "Recovered source quote mismatch")
    pairs = []
    for item in report["executables"]["items"]:
        witness = targets(report)[item["finding_id"]]
        anchors = {witness[side]["source"]["line"] for side in ("first", "second")}
        pairs.append((anchors, item))
    return report, pairs, len(citations)


def official(path, source, allow_restored=False):
    require(sha256(source.read_bytes()).hexdigest() == BCC_INPUT, "Wrong BCC input")
    r, pairs, citations = verify_artifact("official", path, source, allow_restored)
    require(r["run"]["max_workers"] == 6, "BCC pool differs from baseline")
    findings = r["whole"]["findings"]
    matches = {f["id"]: f["question"] for f in findings if re.search(r"\bresult\b", f["question"])
               and all(re.search(rf"\b{code}\b", f["question"]) for code in STATUS_CODES)}
    c = r["response_field_review"]["candidates"]
    a = r["response_field_review"]["assessments"]
    rules = {}
    for t in c:
        field_rulings = next(row["rulings"] for row in a if row["candidate"] == t["id"])
        rules[t["field"]] = {e["http_status"]: rule["policy"] for e in t["errors"]
                             for rule in field_rulings if e["example"] in rule["examples"]}
    errors = {row["start"] + 1: row["keys"] for row in r["example_shapes"] if row["start"] >= 179}
    require(errors == {l: ["message"] for l in (180, 185, 192, 197)}, "Wrong four observed error bodies")
    qa = {x["axis"]: x["status"] for x in r["lifecycle_checks"]["checks"]}
    checks = []
    witness_sources = targets(r)
    for anchors, item in pairs:
        row = {"id": item["finding_id"], "anchors": sorted(anchors), "status": item["status"]}
        if item["status"] == "executed":
            outcome = item["result"]
            row["states"] = [{k: s[k] for k in ("key", "label", "unit")} for s in item["program"]["states"]]
            row["difference"] = outcome["scenario"]["first_difference"]
            row["final"] = outcome["scenario"]["final"]
            row["control"] = outcome["control"]["final"]
            first_line = witness_sources[item["finding_id"]]["first"]["source"]["line"]
            states = [state for state in item["program"]["states"] if state["key"] in item["program"]["observe"]]
            if anchors == {38, 64}:
                observable = [s for s in states if "феврал" in s["label"].lower()]
                if len(observable) == 1:
                    state = observable[0]
                    factor = {"₸": 1, "тенге": 1, "тиын": 100}.get(state["unit"].lower())
                    if factor:
                        expected = (0, 4000 * factor) if first_line == 38 else (4000 * factor, 0)
                        row["matches_independent_oracle"] = tuple(row["final"][k][state["key"]]
                                                                  for k in ("first", "second")) == expected
            if anchors == {39, 40}:
                state = next(s for s in states if s["key"] == row["difference"]["field"])
                expected = (1, 2) if first_line == 39 else (2, 1)
                values = tuple(row["final"][k][state["key"]] for k in ("first", "second"))
                row["matches_independent_oracle"] = values == expected and any(
                    term in state["label"].lower() for term in ("уведомлен", "push"))
        else:
            row["reason"] = item["reason"]
        checks.append(row)
    # Report misses as values: a failed experiment remains replayable.
    try:
        check_protocol("official", r, pairs)
        bcc_pairs = True
    except ValueError:
        bcc_pairs = False
    return {"sha256": sha256(path.read_bytes()).hexdigest(), "timing": timing(r), "citations": citations,
            "result_questions": matches, "rulings": rules, "errors": errors,
            "qa": qa, "qa_matches_expected_status": qa == QA_EXPECTED,
            "both_source_pairs_executed": bcc_pairs, "witnesses": checks}


def planetarium(folder, name, allow_restored=False):
    source = FIELD / (name + ".md")
    path = folder / (name + ".json")
    require(sha256(source.read_bytes()).hexdigest() == fields.INPUTS[name], "Planetarium source changed")
    report, _, citations = verify_artifact(name, path, source, allow_restored)
    require(report["run"]["max_workers"] == 4, "Planetarium pool differs from baseline")
    observed = {}
    for shape in report["example_shapes"]:
        line = shape["start"] + 1
        op, status, keys = fields.EXAMPLES[line]
        raw_keys = list(json.loads(source.read_text().splitlines()[line - 1]))
        require(shape["keys"] == raw_keys == list(keys) and shape["key_count"] == len(keys), "Wrong API shape")
        require(op in shape["section"] and f"HTTP {status}" in shape["section"], "Wrong example operation/status")
        observed[line] = (op, status, raw_keys)
    require(set(observed) == set(fields.EXAMPLES), "Example missing")
    return {"sha256": sha256(path.read_bytes()).hexdigest(), "timing": timing(report), "citations": citations,
            "examples": observed, "policy": fields.obligations(report, name.endswith("-aligned")),
            "target_questions": fields.final_questions(report),
            "all_owner_questions": {f["id"]: f["question"] for f in report["whole"]["findings"]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Original BCC requirements.md")
    parser.add_argument("cohort", type=Path, help="Directory containing the three candidate JSONs")
    parser.add_argument("--allow-restored", action="store_true",
                        help="Check semantics of resumed documents; they are ineligible for cold timings")
    args = parser.parse_args()
    result = {}
    with TemporaryDirectory() as tmp:
        before = Path(tmp) / "official.json"
        before.write_bytes(subprocess.check_output(["git", "show", BASELINE + ":runs/bcc-release/review.json"], cwd=ROOT))
        require(sha256(before.read_bytes()).hexdigest() == OLD_OFFICIAL, "Baseline bytes changed")
        for label, path, folder in (("before", before, FIELD / "runs/release"),
                                    ("after", args.cohort / "official.json", args.cohort)):
            result[label] = {"official": official(path, args.source, args.allow_restored)}
            for name in fields.INPUTS:
                result[label][name] = planetarium(folder, name, args.allow_restored)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
