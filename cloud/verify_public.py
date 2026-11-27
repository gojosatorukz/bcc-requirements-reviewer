"""Open the jury release as a new HTTPS visitor; do not call a model."""

from hashlib import sha256
from http.cookiejar import CookieJar
import json
from pathlib import Path
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener


ORIGIN = "https://razbor-tz.onrender.com"
ROOT = Path(__file__).resolve().parents[1]


def main():
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
    manifest = json.loads((ROOT / "runs/bcc-release/materials.json").read_bytes())
    for filename in ("review.html", "review.pdf", "review.json"):
        assert sha256(read("/reports/demo/" + filename)).hexdigest() == manifest[filename], filename
    print("Ready HTML, PDF and JSON match the release.", flush=True)
    gemini = json.loads(read("/reports/demo/gemini.json"))
    assert gemini["run"]["model"].startswith("gemini-")
    assert sha256(read("/reports/demo/gemini.html")).digest() == sha256(
        (ROOT / "eval/gemini/behavior/bcc-release.html").read_bytes()).digest()
    assert "Проверить структуру без ИИ" in read("/").decode()
    print("Gemini examples and the upload form open.", flush=True)

    sample = b'# Export\n| Key | Unit |\n| --- | --- |\n| rate | ms |\n~~~json\n{"delay":3,}\n~~~'
    broken = check_file("queue.md", sample)
    assert (broken["lines"], broken["tables"], broken["examples"]) == (7, 1, 1), broken
    assert [item["line"] for item in broken["diagnostics"]] == [6], broken
    assert check_file("queue.md", sample.replace(b"3,}", b"3}"))["diagnostics"] == []
    print("Fresh specification: a JSON error at line 6 disappears after correction. No LLM request was made.")


if __name__ == "__main__":
    main()
