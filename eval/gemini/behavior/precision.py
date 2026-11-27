"""Replay the production dispute check on frozen Gemini reports, including false agreement signals."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from eval.score_review import score_report
from reviewer import behavior, codex_provider, source_refs
from reviewer.contract import validate_shape
from reviewer.executable import verify_source
from reviewer.full import call_stage, check_critical_sources, check_whole, packed, reconcile_examples
from reviewer.full_render import validate_report


ROOT = Path(__file__).resolve().parent
CASES = ("museum-aligned", "museum", "bcc")


def read(path):
    return json.loads(path.read_bytes())


def replay(case, records, model=None):
    path = ROOT / (case + "-release.json")
    original = read(path)
    source = validate_report(original)
    if case == "bcc":
        if not all(c["structural_check_passed"] for c in score_report(original, "bcc")["checks"]):
            raise ValueError("Исходный банковский контроль не проходит")
    else:
        verify_source(original, ROOT / (case + ".md"))
    before = original["whole"]
    whole = deepcopy(before)
    if case == "museum-aligned":
        stages = read(ROOT / "museum-aligned-stage-outputs.json")
        if any(stages[k] != original["run"][k] for k in ("input_sha256", "model", "engine_sha256")):
            raise ValueError("Ответы стадий относятся к другому прогону")
        bases = {p["example"]: p["bases"] for p in stages["Прогноз по правилам"]["predictions"]}
        agreements = [dict(c, rule_lines=bases[c["example"]]) for c in stages["Сверка примеров"]["checks"]
                      if c["verdict"] == "agrees"]
        origin = "recorded_gemini_agreements"
    else:
        # Deliberately wrong agreement for each real critical witness, including rule vs rule.
        agreements = []
        for finding in whole["findings"]:
            for w in finding["triage"]["witness"]:
                agreements.append({"example": w["second"]["source"]["line"],
                                   "rule_lines": [w["first"]["source"]["line"]]})
        origin = "injected_false_agreements"
    targets = behavior.disputed_examples(whole, agreements, original["conflicts"])
    if len(targets) != (1 if case == "museum-aligned" else 2):
        raise ValueError("Контроль не дошёл до сверяющего")

    log_path = records / (case + ".json")
    metadata = {"original_report_sha256": sha256(path.read_bytes()).hexdigest(),
                "input_sha256": original["run"]["input_sha256"], "original_model": original["run"]["model"],
                "agreement_origin": origin}
    if model:
        record = {**metadata, "started_utc": datetime.now(timezone.utc).isoformat(),
                  "judge_provider": codex_provider.provider_name(), "judge_model": model, "stages": {}}
    else:
        record = read(log_path)
        if any(record[k] != v for k, v in metadata.items()):
            raise ValueError("Оригинальный прогон не совпадает с паспортом проверки")

    seen = []
    def stage(name, prompt, data, schema, verify):
        digest = sha256(packed([prompt, data, schema]).encode()).hexdigest()
        seen.append(name)
        if model:
            result, events = call_stage(name, prompt, data, schema, verify, model, None, "")
            record["stages"][name] = {"input_digest": digest, "result": result, "attempts": events}
            records.mkdir(parents=True, exist_ok=True)
            log_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        else:
            saved = record["stages"][name]
            if digest != saved["input_digest"]:
                raise ValueError(f"Изменились данные или инструкция для стадии {name}")
            result, events = saved["result"], saved["attempts"]
            validate_shape(result, schema, name)
            verify(result)
        return result, events

    refs, extra = source_refs.references(source)
    def anchored(name, prompt, data, schema, verify):
        if extra:
            data = {**data, "source_fragments": extra}
        wire, events = stage(name, prompt + source_refs.INSTRUCTIONS, data, source_refs.wire_schema(schema),
                             lambda r: verify(source_refs.expand(r, refs)))
        return source_refs.expand(wire, refs), events

    reconcile_examples(whole, original["conflicts"], agreements, source, stage, anchored)
    check_whole(whole, original["registry"], original["conflicts"], source,
                http_status_policy=original["http_status_policy"])
    check_critical_sources(whole, original["conflicts"])
    if set(seen) != set(record["stages"]):
        raise ValueError("Не все сохранённые стадии использованы")
    first, last = ([f["id"] for f in w["findings"] if f["priority"] == "критический"] for w in (before, whole))
    if case == "museum-aligned":
        if first != ["D-01"] or whole["findings"] or len(whole["reconciled"]) != len(before["reconciled"]) + 1:
            raise ValueError("Ложная блокировка осталась либо решение потеряно")
    elif whole != before:
        raise ValueError("Ложный сигнал о согласии изменил действительное замечание или вопрос")
    answers = record["stages"]["Перепроверка исходов"]["result"]["comparisons"]
    uncertain = [c["finding"] for c in answers if not c["covered"]]
    if any(c["covered"] and (c["rule_value"] == c["example_value"]) != (case == "museum-aligned") for c in answers):
        raise ValueError("Вердикт по полностью проверенному сценарию расходится с эталоном")
    if uncertain and case in ("museum-aligned", "bcc"):
        raise ValueError("Проверяющий не воспроизвёл ожидаемые исходы")
    if case == "bcc" and not any(all(x in f["question"] for x in ("result", "400", "401", "404", "500"))
                                 for f in whole["findings"]):
        raise ValueError("Пропал вопрос о результате ошибочных ответов")
    return {"case": case, "origin": origin, "before": first, "after": last, "judge": record["judge_model"],
            "abstentions": uncertain, "outcomes": [[c["values"][c[k] - 1] for k in ("rule_value", "example_value")] for c in answers],
            "readiness": [whole[k]["status"] for k in ("development", "testing")]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", choices=CASES, nargs="+")
    parser.add_argument("--records", type=Path, default=ROOT / "precision")
    parser.add_argument("--live-model", help="Request new verdicts from the configured provider and record them")
    args = parser.parse_args()
    for fixture in args.cases:
        print(json.dumps(replay(fixture, args.records, args.live_model), ensure_ascii=False), flush=True)
