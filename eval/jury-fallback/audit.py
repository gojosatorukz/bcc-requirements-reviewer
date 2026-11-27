"""Check the jury site's downloaded report and grounded packing controls offline."""

import argparse
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re

from reviewer.executable import verify_source
from reviewer.full_render import validate_report


ROOT = Path(__file__).resolve().parent
HASHES = {
    "packing-v1": "ba9c14901486dd53c305c269519e73f5af4b8789e99f30bae1af61f8fbe51aab",
    "packing-v2": "1c9d4d27cee39eaaa979df6701b23fa9baa6ab43bae0a5b919fee52a648a09d1",
}


class ReportHTML(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids = set()
        self.links = set()
        self.text = []
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        href = attrs.get("href") or ""
        if tag == "a" and href.startswith("#"):
            self.links.add(href[1:])

    def handle_data(self, data):
        self.text.append(data)


def source_pair(finding, row):
    return any(
        {witness[side]["source"]["line"] for side in ("first", "second")} == {4, row}
        for witness in finding["triage"]["witness"]
    )


def box_trace(item):
    if item["status"] != "executed":
        return {"status": item["status"]}
    result = item["result"]
    diff = result["scenario"]["first_difference"]
    if not diff:
        return {"status": "no_difference"}
    fields = {f["key"]: f for f in item["program"]["states"]}
    field = fields[diff["field"]]
    control = result["control"]["final"]
    left, right = (control[side][field["key"]] for side in ("first", "second"))
    def has_input(case, quantity):
        values = [*case["initial"].values(), *(v for step in case["steps"] for v in step["inputs"].values())]
        return quantity in values
    matches = (bool(re.search(r"ящик|короб|box|crate", field["label"] + " " + field["unit"], re.I))
               and {diff["first"], diff["second"]} == {3, 4} and has_input(result["scenario"], 13))
    return {"status": "executed", "observable": field["label"], "values": [diff["first"], diff["second"]],
            "verified_at_13": matches, "control_values": [left, right],
            "verified_at_12": matches and left == right == 3 and has_input(result["control"], 12)}


def assess(fixture, report, html, provider=None):
    path = ROOT / (fixture + ".md")
    if sha256(path.read_bytes()).hexdigest() != HASHES[fixture]:
        raise ValueError("Изменились исходные байты контрольного документа")
    if report.get("schema_version") != 2:
        raise ValueError("Ожидался полный отчёт формата 2")
    verify_source(report, path)
    lines = validate_report(report)
    run = report["run"]
    findings = report["whole"]["findings"]
    page = ReportHTML(html)
    text = " ".join(" ".join(page.text).split())
    expected_anchors = {f"source-line-{n}" for n in lines} | {f["id"] for f in findings}
    if expected_anchors - page.ids or page.links - page.ids:
        raise ValueError("В HTML пропущена строка, карточка или цель внутренней ссылки")
    if any(str(item) not in text for item in (run["input_sha256"], run["model"], run["started_utc"])):
        raise ValueError("В паспортах HTML и JSON различаются документ, модель или запуск")
    if any(" ".join(f["question"].split()) not in text for f in findings):
        raise ValueError("HTML не содержит вопрос из итогового JSON")
    if provider and provider != run["provider"]:
        raise ValueError(f"Ожидался провайдер {provider}, получен {run['provider']}")
    known = [f for f in findings if f["category"] == "нарушение"
             and f["priority"] == "критический" and source_pair(f, 11)]
    relevant = [f for f in known if "?" in f["question"] and re.search(
        r"ящик|короб|упаков|округл|остат|вместимост|неполн", f["question"], re.I)]
    false_rows = [10] if fixture == "packing-v1" else [10, 11]
    false_claims = [f["id"] for f in findings if f["category"] == "нарушение"
                    and any(source_pair(f, row) for row in false_rows)]
    # A lower-priority violation may have no structured witness. Do not report a clean
    # control if its evidence alone points to that rule and row; surface it for a reader.
    untriaged = []
    for f in findings:
        if f["category"] != "нарушение" or f["triage"]["witness"]:
            continue
        cited = {e["line"] for e in f["evidence"]}
        if any({4, row} <= cited for row in false_rows):
            untriaged.append(f["id"])
    extras = [f["id"] for f in findings if f["priority"] == "критический" and f not in known
              and f["id"] not in false_claims]
    expected_count = int(fixture == "packing-v1")
    traces = {i["finding_id"]: box_trace(i) for i in report.get("executables", {}).get("items", [])
              if any(f["id"] == i["finding_id"] for f in known)}
    return {"fixture": fixture, "provider": run["provider"], "model": run["model"],
            "agent_version": run["agent_version"], "fresh_calls": run["fresh_calls"],
            "restored_attempts": run["restored_attempts"], "seconds": run["elapsed_seconds"],
            "critical_target": [f["id"] for f in known], "missed_expected_critical": bool(expected_count and not known),
            "questions": [f["question"] for f in relevant], "false_conflicts_on_controls": false_claims,
            "other_criticals_for_manual_review": extras, "control_violations_for_manual_review": untriaged, "verified_programs": traces,
            "passed": (run["fresh_calls"] > 0 and len(known) == len(relevant) == expected_count
                       and not false_claims and not untriaged and not extras)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", choices=HASHES)
    parser.add_argument("report", type=Path)
    parser.add_argument("html", type=Path)
    parser.add_argument("--provider")
    args = parser.parse_args()
    result = assess(args.fixture, json.loads(args.report.read_bytes()), args.html.read_text(encoding="utf-8"),
                    args.provider)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
