"""Offline acceptance of the browser's downloaded artifacts and the frozen bindery example."""

import argparse
from datetime import datetime, timedelta
from hashlib import sha256
from importlib import import_module
import json
from pathlib import Path
import re

from reviewer.executable import verify_source
from reviewer.full_render import validate_report


ROOT = Path(__file__).resolve().parent
HASHES = {"site": "ae8fb9194865549f33a06e213b6b421c4f94ddfef1fb83c5e35b14daa24b2295",
          "local": "25a54aa29e17c3de0d24dd1d2adaf76d100671dcbf74df26b9cc528c200a38d4"}
ReportHTML = import_module("eval.jury-fallback.audit").ReportHTML


def require(condition, message):
    if not condition:
        raise ValueError(message)


def passport(run, receipt, route):
    require(run["provider"] == "cloudflare-workers-ai", "В отчёте другой провайдер")
    require(isinstance(run["model"], str) and run["model"].strip(), "Нет модели запасного провайдера")
    previous = run.get("fallback_from") or {}
    require(previous.get("provider") == "gemini-api" and previous.get("reason") == "HTTP 429"
            and str(previous.get("model", "")).startswith("gemini-"), "Не подтверждён переход после квоты Gemini")
    fresh = [item for item in run["attempts"] if not item.get("restored")]
    require(type(run["fresh_calls"]) is int and run["fresh_calls"] == len(fresh) > 0
            and any(item["status"] == "accepted" for item in fresh), "Нет подтверждённого нового обращения")
    start, finish, model_start = (datetime.fromisoformat(time.replace("Z", "+00:00")) for time in
                                 (receipt["started_utc"], receipt["downloaded_utc"], run["started_utc"]))
    require(start - timedelta(minutes=2) <= model_start <= finish + timedelta(minutes=2),
            "Время прогона вне интервала браузерной проверки")
    endpoint = "/api/project-runs" if route == "local" else "/api/runs"
    require(receipt["route"] == route and receipt["endpoint"] == endpoint and receipt["submit_status"] == 202
            and receipt["job_state"] == "done" and receipt["upload_sha256"] == HASHES[route],
            "Квитанция формы не подтверждает новый полный запуск")
    if route == "local":
        require(receipt["execution"] == "project" and re.fullmatch(r"[a-f0-9]{40,64}", receipt["checkout_revision"]),
                "Не подтверждён путь через сервер проекта из чистой копии")


def grounded_quality(findings, threshold):
    hits, control, to_read = [], [], []
    for finding in findings:
        if finding["category"] != "нарушение":
            continue
        pairs = [{example[side]["source"]["line"] for side in ("first", "second")}
                 for example in finding.get("triage", {}).get("witness", [])]
        evidence = {row["line"] for row in finding["evidence"]}
        if any({4, 10} == pair for pair in pairs) or (not pairs and evidence == {4, 10}):
            control.append(finding["id"])
        if {4, 11} <= evidence or {4, 11} in pairs:
            question = finding["question"]
            authored = " ".join(str(finding.get(key, "")) for key in ("question", "reason", "impact", "title", "action"))
            for witness in finding.get("triage", {}).get("witness", []):
                authored += " " + " ".join(str(witness[side].get("outcome", "")) for side in ("first", "second"))
            alternatives = re.search("швейн|прошив|сши[вт]", authored, re.I) and re.search("кле[йеи]|скле", authored, re.I)
            if (alternatives and "?" in question and re.search(rf"(?<!\d){threshold}(?!\d)", authored)
                    and re.search(r"швейн|клеев|участ|талон|маршрут|печат|границ|порог|толщин|кореш", question, re.I)):
                hits.append({"id": finding["id"], "question": question,
                             "names_threshold": bool(re.search(rf"(?<!\d){threshold}(?!\d)", question))})
            else:
                to_read.append(finding["id"])
    return {"matched": hits, "other_candidates_for_manual_review": to_read, "false_on_aligned_row": control,
            "passed": bool(hits) and not control}


def check(route, directory):
    fixture = ROOT / f"bindery-{route}.md"
    require(sha256(fixture.read_bytes()).hexdigest() == HASHES[route], "Изменились байты независимого ТЗ")
    receipt = json.loads((directory / "browser.json").read_bytes())
    raw = (directory / "review.json").read_bytes()
    markup = (directory / "review.html").read_bytes()
    require(sha256(raw).hexdigest() == receipt["json_sha256"] and sha256(markup).hexdigest() == receipt["html_sha256"],
            "Артефакты отличаются от скачанных браузером байтов")
    report = json.loads(raw)
    require(type(report.get("schema_version")) is int and report["schema_version"] == 2
            and "report_type" not in report, "Ожидается полный отчёт версии 2")
    run = report["run"]
    require(run["input_name"] == fixture.name, "Имя загруженного файла отличается")
    verify_source(report, fixture)
    lines = validate_report(report)
    passport(run, receipt, route)
    findings = report["whole"]["findings"]
    page = ReportHTML(markup.decode("utf-8"))
    text = " ".join(" ".join(page.text).split())
    required_ids = {f"source-line-{n}" for n in lines} | {f["id"] for f in findings}
    require(not required_ids - page.ids and not page.links - page.ids, "В HTML отсутствует строка, замечание или ссылка")
    required_text = [run[key] for key in ("input_sha256", "model", "started_utc")] + [f["question"] for f in findings]
    require(all(" ".join(value.split()) in text for value in required_text), "Паспорт или вопросы HTML не совпадают с JSON")
    return {"delivery_passed": True, "input_sha256": HASHES[route], "model": run["model"],
            "provider": run["provider"], "fallback_from": run["fallback_from"], "fresh_calls": run["fresh_calls"],
            "quality": grounded_quality(findings, 18 if route == "site" else 23)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("route", choices=HASHES)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        result = check(args.route, args.directory)
    except (ValueError, KeyError, OSError, TypeError) as exc:
        result = {"delivery_passed": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.get("quality", {}).get("passed") else 1)
