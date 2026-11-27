"""Repeat the offline checks of the frozen BCC PDF, with optional Markdown comparison."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from reviewer.contract import HTTP_STATUS_POLICY, source_http_statuses
from reviewer.executable import verify_source
from reviewer.full_render import validate_report
from reviewer.input import load_document
from reviewer.json_examples import example_body, inspect_examples
from reviewer.segments import segment

PDF_SHA = "6e54b30a9e09e4120db789983561d066ac820a86387c6aa7b8a10f17e9506e9c"
MARKDOWN_SHA = "e7dae55e5453a250d93e335f5ff9c7f775f4457ccddd333f4a67e47b20d74381"
EXPECTED = [
    {"result": "OK", "amount": "50000", "enabled": "1"},
    {"result": "ERROR", "message": "Проверьте сумму"},
    {"message": "Некорректный запрос"}, {"message": "Требуется вход"},
    {"message": "Карта не найдена"}, {"message": "Ошибка сервиса"},
]
# Physical source lines in this PDF, fixed before the successful cold run.
PAIRS = {"month": ({29, 30, 31}, {56}), "push": ({32, 33}, {34, 35})}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def examples(lines, segments):
    return [json.loads(example_body(lines, unit["start"], unit["end"])[1])
            for unit in segments if unit["kind"] == "пример json"]


def table_rows(lines, segments):
    def clean(text):
        return [re.sub(r"\s+", " ", cell.replace("`", "").replace("**", "")).strip()
                for cell in text.strip("|").split("|")]
    return [(b["start"], clean(lines[b["start"] - 1])) for b in segments
            if b["kind"] in {"шапка таблицы", "строка таблицы"}]


def pair_name(witness):
    first, second = (witness[key]["source"]["line"] for key in ("first", "second"))
    for label, (a, b) in PAIRS.items():
        if (first in a and second in b) or (first in b and second in a):
            return label
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", nargs="?", type=Path, default=HERE / "review.json")
    parser.add_argument("--markdown", type=Path, help="Optional untouched organizer Markdown")
    args = parser.parse_args()
    pdf = HERE / "requirements.pdf"
    report = json.loads(args.report.read_bytes())
    validate_report(report)
    require(verify_source(report, pdf) == PDF_SHA, "Unexpected PDF bytes")
    lines, _, meta = load_document(pdf)
    segments = segment(lines, meta["pdf_json_blocks"])
    counts = Counter(item["kind"] for item in segments)
    require(meta["page_count"] == 5 and counts["шапка таблицы"] == 7 and counts["строка таблицы"] == 44,
            "Page or table structure differs")
    require(examples(lines, segments) == EXPECTED, "JSON values differ")
    checks = inspect_examples(lines, segments)["examples"]
    expected_rows = [137, 139, 144, 146, 150, 152]
    require([(row["start"], row["status"]) for row in checks] == [(n, "valid") for n in expected_rows],
            "JSON location or syntax differs")
    require(source_http_statuses(lines, HTTP_STATUS_POLICY) == {"200", "201", "400", "401", "403", "404", "500"},
            "Unexpected HTTP statuses")
    print("5 pages, 7 table headers, 44 table rows, 7 HTTP codes; six exact JSON objects at", expected_rows)
    if args.markdown:
        original, sha, _ = load_document(args.markdown)
        require(sha == MARKDOWN_SHA, "Different organizer Markdown")
        document = segment(original)
        require(examples(original, document) == EXPECTED, "Different Markdown examples")
        left, right = table_rows(original, document), table_rows(lines, segments)
        require(len(left) == len(right), "Table row count differs")
        spaces = []
        for (old_n, old), (new_n, new) in zip(left, right):
            require([v.replace(" ", "") for v in old] == [v.replace(" ", "") for v in new],
                    f"Cell or non-space character changed: Markdown {old_n}, PDF {new_n}")
            if old != new:
                spaces.append((old_n, new_n))
        print("Cell contents match after whitespace removal; soft-wrap differences (Markdown, PDF):", spaces)

    candidates = {pair_name(w) for w in report["conflicts"]}
    findings = {f["id"]: f for f in report["whole"]["findings"]}
    observed = set()
    for item in report.get("executables", {}).get("items", []):
        if item["status"] != "executed":
            continue
        program, result = item["program"], item["result"]
        name = pair_name(program)
        if not name:
            continue
        difference = result["scenario"]["first_difference"]
        require(difference and result["control"]["first_difference"] is None, f"Missing contrast for {name}")
        require(all(step["first"] == step["second"] for step in result["control"]["steps"]),
                f"Control mismatch for {name}")
        field = next(s for s in program["states"] if s["key"] == difference["field"])
        n = [program[k]["source"]["line"] for k in ("first", "second")]
        finding = findings[item["finding_id"]]
        print(f"{name}: {finding['id']} ({finding['priority']}), PDF {n}, step {difference['step']}, "
              f"{field['label']}: {difference['first']} / {difference['second']} {field['unit']}; control agrees")
        observed.add(name)
    require(PAIRS.keys() <= candidates, f"Missing discovery: {sorted(PAIRS.keys() - candidates)}")
    require(PAIRS.keys() <= observed, f"Missing executable: {sorted(PAIRS.keys() - observed)}")


if __name__ == "__main__":
    main()
