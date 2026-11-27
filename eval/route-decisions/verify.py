"""Compare recorded Cloudflare reviews to the two pre-registered cases, entirely offline."""

from copy import deepcopy
import json
from pathlib import Path

from reviewer.executable import simulate, verify_source
from reviewer.full_render import validate_report


HERE = Path(__file__).resolve().parent
CASES = (
    ("Мастерская", HERE.parent / "hosted-reserve/bindery-site.md",
     HERE.parent / "hosted-reserve/runs/site", HERE / "runs/workshop-after",
     18, 17, "Швейный участок", "Клеевой участок", "Клеевой участок"),
    ("Табло", HERE / "light-meter.md", HERE / "runs/light-before", HERE / "runs/light-after",
     40, 55, "День", "Сумерки", "День"),
)


def check():
    for title, source, before, after, boundary, control, general_label, example_label, agreed in CASES:
        for directory in (before, after):
            report = json.loads((directory / "review.json").read_bytes())
            run = report["run"]
            validate_report(report)
            verify_source(report, source)
            assert run["provider"] == "cloudflare-workers-ai" and run["fresh_calls"] > 0
            assert run["fallback_from"]["reason"] == "HTTP 429"
            item, = report["executables"]["items"]
            print(title, directory.name, item["status"], run["elapsed_seconds"], "s")
            if directory == before and title == "Мастерская":
                assert item["status"] == "unsupported"
                continue
            assert item["status"] == "executed"
            program = item["program"]
            rule, = (s for s in ("first", "second") if program[s]["source"]["line"] == 4)
            acceptance = "second" if rule == "first" else "first"
            key, = program["observe"]
            for case, value, expected_rule, expected_example in (
                    ("scenario", boundary, general_label, example_label), ("control", control, agreed, agreed)):
                event, = program[case]["events"]
                assert event["values"][0]["value"] == value
                trace = simulate(report, item["finding_id"], program[case])
                assert trace == item["result"][case]
                assert (trace["final"][rule][key], trace["final"][acceptance][key]) == (expected_rule, expected_example)
            other = deepcopy(program["scenario"])
            other["events"][0]["values"][0]["value"] = boundary + 1
            if directory == before:
                assert simulate(report, item["finding_id"], other)["final"][acceptance][key] == agreed
                continue
            assert "route_proof" in item
            try:
                simulate(report, item["finding_id"], other)
            except ValueError as exc:
                assert "нет подтверждённого результата" in str(exc)
            else:
                raise AssertionError("Unlisted acceptance input treated as supported")
            altered = deepcopy(report)
            altered["executables"]["items"][0]["route_proof"]["readings"]["scenario"][rule]["value"] = example_label
            try:
                validate_report(altered)
            except ValueError:
                pass
            else:
                raise AssertionError("Altered prediction accepted")
            html = (directory / "review.html").read_text()
            assert all(v in html for v in (general_label, example_label, 'id="execution-' + item["finding_id"] + '"',
                                           run["input_sha256"], "два измерения из приёмки"))


if __name__ == "__main__":
    check()
