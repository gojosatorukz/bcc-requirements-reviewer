"""Open the jury release as a new HTTPS visitor; do not call a model."""

import argparse
import csv
from hashlib import sha256
from http.cookiejar import CookieJar
from io import StringIO
import json
from pathlib import Path
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener


ORIGIN = "https://razbor-tz.onrender.com"
ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-holdout", action="store_true",
                        help="import both synthetic Astra reviews into this temporary visitor session")
    options = parser.parse_args()
    client = build_opener(HTTPCookieProcessor(CookieJar()))

    def read(path, **arguments):
        with client.open(Request(ORIGIN + path, **arguments), timeout=45) as response:
            return response.read()

    def check_file(name, document):
        return json.loads(read("/api/structure", data=document, headers={
            "Origin": ORIGIN, "X-File-Name": name, "Content-Type": "application/octet-stream"}))

    deadline = monotonic() + 120
    while True:
        try:
            if read("/healthz") == b"ok":
                break
        except (URLError, TimeoutError):
            pass
        if monotonic() > deadline:
            raise RuntimeError("The public service did not wake up")
        sleep(3)

    # Go directly to the report first, as a judge following a shared link would.
    manifest = json.loads((ROOT / "runs/bcc-jury/materials.json").read_bytes())
    for filename in ("review.html", "review.pdf", "review.json"):
        assert sha256(read("/reports/demo/" + filename)).hexdigest() == manifest[filename], filename
    print("Ready HTML, PDF and JSON match the release.", flush=True)
    for filename in ("dialogue.html", "wrong-revision.html"):
        page = read("/reports/demo/owner/" + filename)
        assert page == (ROOT / "runs/bcc-jury/owner" / filename).read_bytes(), filename
    print("Both owner dialogue and revision pages match this run.", flush=True)
    demo_hash = manifest["review.json"]
    current = read("/reports/demo/review.html?sha256=" + demo_hash).decode()
    assert current.count('class="execution-trace execution-boundary"') == 2
    report = json.loads((ROOT / "runs/bcc-jury/review.json").read_bytes())
    for item in report["executables"]["items"]:
        if item["status"] == "executed":
            fid = item["finding_id"]
            for case_name in ("control", "scenario"):
                case = item["program"][case_name]
                reply = json.loads(read(f"/api/simulate/demo/{demo_hash}/{fid}",
                    data=json.dumps({"case": case}).encode(),
                    headers={"Origin": ORIGIN, "Content-Type": "application/json"}))
                assert reply["trace"] == item["result"][case_name], (fid, case_name)
                assert reply["explanation_html"], (fid, case_name)
    print("The browser's simulation endpoint reproduced the saved scenarios and controls.", flush=True)

    def tickets(report_id, digest):
        raw = read(f"/reports/{report_id}/issues.csv?sha256={digest}")
        return list(csv.DictReader(StringIO(raw.decode("utf-8-sig"))))
    findings = report["whole"]["findings"]
    demo_tickets = tickets("demo", demo_hash)
    assert len(demo_tickets) == len(findings)
    assert all(f["question"] in task["Description"] for task, f in zip(demo_tickets, findings))
    print(f"Current bank report exposes both replay boundaries and {len(demo_tickets)} Jira tasks.", flush=True)
    gemini = json.loads(read("/reports/demo/gemini.json"))
    assert gemini["run"]["model"].startswith("gemini-")
    assert sha256(read("/reports/demo/gemini.html")).digest() == sha256(
        (ROOT / "eval/gemini/behavior/bcc-release.html").read_bytes()).digest()
    homepage = read("/").decode()
    assert "Проверить структуру без ИИ" in homepage
    assert "/reports/rehearsal/openai.html#execution-D-01" in homepage
    for extension in ("html", "json"):
        assert read(f"/reports/rehearsal/openai.{extension}") == (
            ROOT / "eval/jury-walkthrough" / f"openai.{extension}").read_bytes()
    print("The full hosted Sol report on the music school opens in HTML and JSON.", flush=True)
    print("Gemini examples and the upload form open.", flush=True)

    sample = b'# Export\n| Key | Unit |\n| --- | --- |\n| rate | ms |\n~~~json\n{"delay":3,}\n~~~'
    broken = check_file("queue.md", sample)
    assert (broken["lines"], broken["tables"], broken["examples"]) == (7, 1, 1), broken
    assert [item["line"] for item in broken["diagnostics"]] == [6], broken
    assert check_file("queue.md", sample.replace(b"3,}", b"3}"))["diagnostics"] == []
    print("Fresh specification: a JSON error at line 6 disappears after correction. No LLM request was made.")

    if options.with_holdout:
        for case, stage, folder in (
                ("bindery", "before", "eval/hosted-reserve/runs/site"),
                ("bindery", "after", "eval/route-decisions/runs/workshop-after"),
                ("reading-room", "before", "eval/route-decisions/runs/light-before"),
                ("reading-room", "after", "eval/route-decisions/runs/light-after"),
                *((case, stage, f"eval/retained-values/runs/{case}-{stage}")
                  for case in ("studio-pickup", "caption-export") for stage in ("before", "assisted"))):
            for extension in ("json", "html"):
                assert read(f"/reports/{case}/{stage}.{extension}") == (ROOT / folder / ("review." + extension)).read_bytes()
            print(f"Public {case}/{stage}: archived HTML and JSON match.")
        for stage, saved in (("partial", "after"), ("corrected", "control")):
            for extension in ("json", "html"):
                assert read(f"/reports/spectrometer/{stage}.{extension}") == (ROOT / "eval/quota-review" / f"{saved}.{extension}").read_bytes()
        print("Both public partial reports match the browser downloads.")
        samples = [(case, stage, ROOT / f"{folder}-{variant}")
                   for case, folder in (("packing", "eval/jury-fallback/runs/astra"),
                                        ("recorder", "eval/fixed-acceptance/results/recorder"))
                   for variant, stage in (("v1", "before"), ("v2", "after"))]
        for case, stage, file in samples:
            raw = file.with_suffix(".json").read_bytes()
            assert read(f"/reports/{case}/{stage}.json") == raw
            archived = file.with_suffix(".html").read_bytes()
            assert read(f"/reports/{case}/{stage}.html") == archived
            receipt = json.loads(read("/api/import-report", data=raw, headers={
                "Origin": ORIGIN, "Content-Type": "application/json"}))
            digest = sha256(raw).hexdigest()
            assert receipt["sha256"] == digest
            path = f'/reports/{receipt["id"]}/review'
            assert read(path + ".json") == raw
            html = read(path + ".html").decode()
            report = json.loads(raw)
            assert report["run"]["input_sha256"] in html
            findings = report["whole"]["findings"]
            assert all(f["question"] in html for f in findings)
            assert len(tickets(receipt["id"], digest)) == len(findings)
            if case == "recorder" and stage == "before":
                assert "33 Вт·ч" in html and "37 Вт·ч" in html and "#source-line-11" in html
            print(f"Imported {case}/{stage}: full HTML, original JSON and {len(findings)} Jira tasks.")


if __name__ == "__main__":
    main()
