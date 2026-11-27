"""Inspect all archived reports against their independent input documents without an LLM."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from eval.executable.audit import check_protocol, require, verify


INPUT_SHA = {
    "official": "e7dae55e5453a250d93e335f5ff9c7f775f4457ccddd333f4a67e47b20d74381",
    "winding": "ddf75368ef48daed0c5f52bcc3ab852ad7a8b6f0f7ec19645a6e72c679583727",
    "winding-aligned": "c93498762cbf44c1728b8021c34426d31013b3ac63b5fb5e42fb86b636a8bfea",
    "catalogue": "7d7e017166712312c55568491c8aa32ddd634c2a0a78192c1ae481ca0fcdbbf1",
}
BASELINE = "b6243ba64e7b3f120418be8464379f54b00d0f5753f8dde11873445c7edb729b"


def check_bcc_outcomes(pairs):
    for anchors, item in pairs:
        if anchors not in ({38, 64}, {39, 40}):
            continue
        program, result = item["program"], item["result"]
        if anchors == {38, 64}:
            field, = [s for s in program["states"] if s["key"] in program["observe"] and "феврал" in s["label"].lower()]
            factor = {"₸": 1, "тенге": 1, "тиын": 100}[field["unit"]]
            expected, control = (0, 4000 * factor), (4000 * factor, 4000 * factor)
        else:
            field, = [s for s in program["states"] if s["key"] == result["scenario"]["first_difference"]["field"]]
            require(any(term in field["label"].lower() for term in ("push", "уведомлен")), "BCC: неверный счётчик push")
            expected, control = (1, 2), (1, 1)
        for case, values in (("scenario", expected), ("control", control)):
            actual = tuple(result[case]["final"][side][field["key"]] for side in ("first", "second"))
            require(actual == values, f"BCC: {anchors}/{case} ожидалось {values}, получено {actual}")


def inspect(path, case, source):
    require(sha256(source.read_bytes()).hexdigest() == INPUT_SHA[case], f"Wrong source for {case}")
    review, pairs, citations = verify(case, path, source)
    if case == "official":
        check_protocol(case, review, pairs)
        check_bcc_outcomes(pairs)
    findings = review["whole"]["findings"]
    summary = {"report_sha256": sha256(path.read_bytes()).hexdigest(), "input_sha256": INPUT_SHA[case],
               "agent": review["run"]["agent_version"], "seconds": review["run"]["elapsed_seconds"],
               "fresh_calls": review["run"]["fresh_calls"], "citations": citations,
               "findings": len(findings), "registry": len(review["registry"]),
               "priorities": dict(Counter(f["priority"] for f in findings)),
               "executables": []}
    for anchors, item in pairs:
        data = {"id": item["finding_id"], "anchors": sorted(anchors), "status": item["status"]}
        if item["status"] == "executed":
            result = item["result"]
            scenario = result["scenario"]
            field = scenario["first_difference"]["field"]
            state, = (s for s in item["program"]["states"] if s["key"] == field)
            data.update(field=state["label"], unit=state["unit"], first_difference=scenario["first_difference"],
                        final=scenario["final"], control=result["control"]["final"])
        else:
            data["reason"] = item["reason"]
        summary["executables"].append(data)
    if "lifecycle_checks" in review:
        summary["lifecycle_checks"] = review["lifecycle_checks"]
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("official", type=Path, help="Original requirements.md from the organizer")
    parser.add_argument("cohorts", nargs="*", default=["before", "release"], help="Folders under runs/")
    args = parser.parse_args()
    result = {}
    for cohort in args.cohorts:
        result[cohort] = {}
        for name in INPUT_SHA:
            path = HERE / "runs" / cohort / (name + ".json")
            if name == "official" and cohort == "before":
                require(sha256(path.read_bytes()).hexdigest() == BASELINE, "Incorrect baseline report")
            source = args.official if name == "official" else HERE / (name + ".md")
            result[cohort][name] = inspect(path, name, source)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
