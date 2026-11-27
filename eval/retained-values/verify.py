"""Replay the published calculations and compare them with the pre-registered cases."""
from hashlib import sha256
import json
from pathlib import Path

from reviewer.executable import simulate, verify_source
from reviewer.full_render import validate_report

HERE = Path(__file__).resolve().parent
CASES = {"studio-pickup": ({3: "северное", 6: "южное"}, "северное"),
         "caption-export": ({5: "Deutsch", 6: "Español"}, "English")}


def load(path):
    return json.loads(path.read_bytes())


def verify_case(name, rules, control):
    source = HERE / (name + ".md")
    runs = HERE / "runs"
    before = runs / (name + "-before")
    baseline = load(before / "review.json")
    receipt = load(before / "browser.json")
    digest = verify_source(baseline, source)
    validate_report(baseline)
    assert receipt["job_state"] == "done" and receipt["submit_status"] == 202
    assert receipt["upload_sha256"] == digest
    for extension in ("html", "json"):
        assert receipt[extension + "_sha256"] == sha256((before / ("review." + extension)).read_bytes()).hexdigest()
    assert baseline["run"]["provider"] == "cloudflare-workers-ai" and baseline["run"]["fresh_calls"] > 0
    assert baseline["run"]["fallback_from"]["reason"] == "HTTP 429"
    assert baseline["executables"]["items"][0]["status"] == "unsupported"
    assert any({c[s]["source"]["line"]: c[s]["outcome"] for s in ("first", "second")} == rules
               for c in baseline["conflicts"])

    quota = load(runs / (name + "-after-quota") / "browser.json")
    assert quota["upload_sha256"] == digest and quota["submit_status"] == 202
    assert quota["job_state"] == "failed" and "квота резервной модели" in quota["status_text"]

    assisted = runs / (name + "-assisted")
    new = load(assisted / "review.json")
    validate_report(new)
    assert verify_source(new, source) == digest
    assert {k: v for k, v in new.items() if k not in ("execution_run", "executables")} == {
        k: v for k, v in baseline.items() if k != "executables"}
    build = new["execution_run"]
    assert build["base_report_sha256"] == receipt["json_sha256"]
    assert build["provider"] == "codex-cli" and build["model"] == "gpt-6.2-astra" and build["fresh_calls"] > 0
    item, = new["executables"]["items"]
    assert item["status"] == "executed" and item["history_proof"]["readings"]
    program = item["program"]
    field, = program["observe"]
    shown = []
    for case in ("scenario", "control"):
        trace = simulate(new, item["finding_id"], program[case])
        assert trace == item["result"][case]
        assert bool(trace["first_difference"]) == (case == "scenario")
        for side in ("first", "second"):
            value = trace["final"][side][field]
            assert value == (rules[program[side]["source"]["line"]] if case == "scenario" else control)
            if case == "scenario":
                shown.append(value)

    view = load(assisted / "browser.json")
    assert view["route"] == "import" and 200 <= view["status"] < 300
    assert view["import_sha256"] == sha256((assisted / "review.json").read_bytes()).hexdigest()
    assert view["anchor"] == "execution-" + item["finding_id"] and view["displayed"] == shown
    assert view["control"].count(control) >= 2
    assert any(build["model"] in note and baseline["run"]["model"] in note for note in view["modelNote"])
    print(name, "unsupported →", shown, "control:", control, "·", build["elapsed_seconds"], "s")


if __name__ == "__main__":
    for name, (rules, control) in CASES.items():
        verify_case(name, rules, control)
