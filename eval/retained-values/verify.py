"""Replay the site downloads against the pre-registered ground truth with no model calls."""
from hashlib import sha256
import json
from pathlib import Path

from reviewer.executable import simulate, verify_source
from reviewer.full_render import validate_report

HERE = Path(__file__).resolve().parent
# Expected labels follow the original lines, independent of the order of the two sides.
CASES = {"studio-pickup": ({3: "северное", 6: "южное"}, "северное"),
         "caption-export": ({5: "Deutsch", 6: "Español"}, "English")}


def verify():
    for name, (rules, control) in CASES.items():
        versions = []
        for stage in ("before", "after"):
            folder = HERE / "runs" / (name + "-" + stage)
            receipt = json.loads((folder / "browser.json").read_bytes())
            raw = (folder / "review.json").read_bytes()
            html = (folder / "review.html").read_bytes()
            report = json.loads(raw)
            run = report["run"]
            validate_report(report)
            assert verify_source(report, HERE / (name + ".md")) == receipt["upload_sha256"]
            assert receipt["job_state"] == "done" and receipt["submit_status"] == 202
            assert receipt["html_sha256"] == sha256(html).hexdigest()
            assert receipt["json_sha256"] == sha256(raw).hexdigest()
            assert run["provider"] == "cloudflare-workers-ai" and run["fresh_calls"] > 0
            versions.append(run["agent_version"])
            items = report["executables"]["items"]
            print(name, stage, versions[-1], run["elapsed_seconds"], [(i["finding_id"], i["status"]) for i in items])
            if stage == "before":
                continue
            matched = [i for i in items if i["status"] == "executed" and
                       {i["program"][s]["source"]["line"] for s in ("first", "second")} == rules.keys()]
            item, = matched
            program = item["program"]
            field, = program["observe"]
            observed = []
            for case in ("scenario", "control"):
                trace = simulate(report, item["finding_id"], program[case])
                assert trace == item["result"][case]
                for side in ("first", "second"):
                    line = program[side]["source"]["line"]
                    value = trace["final"][side][field]
                    assert value == (rules[line] if case == "scenario" else control), (name, case, side, value)
                    if case == "scenario":
                        observed.append(value)
                assert bool(trace["first_difference"]) == (case == "scenario")
            assert receipt["displayed"] == observed
            assert receipt["anchor"] == "execution-" + item["finding_id"]
            assert control in receipt["control"]
        assert versions[0] != versions[1]


if __name__ == "__main__":
    verify()
