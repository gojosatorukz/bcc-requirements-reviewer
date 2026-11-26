"""Audit fixed conflict controls in a saved reviewer JSON report."""

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from eval.make_bcc_aligned import OFFICIAL_SHA256


@dataclass(frozen=True)
class Control:
    name: str
    expected: bool
    anchors: dict[int, tuple[str, ...]]
    manual_check: str


CALENDAR_RULE = {38: ("дате совершения покупки",)}
CALENDAR_CHECK = (
    "Покупку 31 января провели 1 февраля. По Ф-2 вырос январский итог. "
    "Сверить месяц из приёмочного примера и оба исхода в находке."
)
PUSH = Control(
    "D2", True,
    {39: ("максимум один такой push",), 40: ("независимо от истории предыдущей настройки",)},
    "Push уже отправлен. В том же месяце порог повысили, затем достигли. Сравнить один и два push.",
)
PAYMENT_CHECK = (
    "После двух подтверждений снизили сумму одного платежа, прочие условия прежние. "
    "По строке 9 оба подтверждения действуют, кнопка видна. "
    "По строке 13 проверить, оставляет ли условие для меньшей суммы оба подтверждения в силе."
)
FIXTURES = {
    "bcc": (OFFICIAL_SHA256, (
        Control("D1", True, {**CALENDAR_RULE, 64: ("февральский счётчик",)}, CALENDAR_CHECK), PUSH,
    )),
    "bcc-aligned": ("3d696665cfd491f03441bfc048309777b3a409df412b8b43806f86667adcc10f", (
        Control("D1", False, {**CALENDAR_RULE, 64: ("январский счётчик",)}, CALENDAR_CHECK), PUSH,
    )),
    "payment": ("aeda6760d92c9982f6aa816f5f5030b3c5ab6651222b315cba57bf78d76b6e91", (
        Control("P1", True, {9: ("меньше либо равна",), 13: ("сумма равна",)}, PAYMENT_CHECK),
    )),
    "payment-aligned": ("bdb719e65cc9e07aea91ec1991466e400a055d960bcd73a2623c0983805ebbb9", (
        Control("P1", False, {9: ("меньше либо равна",), 13: ("меньше либо равна",)}, PAYMENT_CHECK),
    )),
}


def evidence_matches(evidence, anchors, source):
    return all(
        any(
            quote["line"] == line and quote["quote"] and quote["quote"] in source[line]
            and all(phrase in quote["quote"].casefold() for phrase in phrases)
            for quote in evidence
        )
        for line, phrases in anchors.items()
    )


def covers_control_lines(evidence, control):
    return control.anchors.keys() <= {quote["line"] for quote in evidence}


def score_report(report, fixture):
    expected_hash, controls = FIXTURES[fixture]
    source = {row["line"]: row["text"] for row in report["source"]}
    line_count = 198 if fixture.startswith("bcc") else 17
    embedded_input = ("\n".join(source.get(line, "") for line in range(1, line_count + 1)) + "\n").encode("utf-8")
    if (
        report["run"]["input_sha256"] != expected_hash
        or hashlib.sha256(embedded_input).hexdigest() != expected_hash
        or not all(1 <= line <= line_count for line in source)
        or len(source) != len(report["source"])
    ):
        raise ValueError("SHA-256 паспорта или встроенного исходника не соответствует набору")

    candidates = {index: candidate for index, candidate in enumerate(report["conflicts"], 1)}
    candidate_evidence = {
        index: [item["first"]["source"], item["second"]["source"]]
        for index, item in candidates.items()
    }
    findings = report["whole"]["findings"] if "whole" in report else None
    critical_ids = set()
    matched_candidates = set()
    checks = []
    for control in controls:
        candidate_ids = {
            index for index, evidence in candidate_evidence.items()
            if evidence_matches(evidence, control.anchors, source)
        }
        matched_candidates.update(candidate_ids)
        matching_findings = [] if findings is None else [
            item for item in findings
            if candidate_ids.intersection(item["candidates"])
            or evidence_matches(item["evidence"], control.anchors, source)
        ]
        critical = [
            item for item in matching_findings
            if item["category"] == "нарушение" and item["priority"] == "критический"
        ]
        critical_ids.update(item["id"] for item in critical)
        violations = [
            item for item in matching_findings if item["category"] == "нарушение"
        ]
        if findings is None:
            claims = len(candidate_ids)
            claim_kind = "candidate"
        elif fixture == "bcc-aligned" and not control.expected:
            claims = len(violations)
            claim_kind = "violation"
        else:
            claims = len(critical)
            claim_kind = "critical_violation"
        same_line_candidates = {
            index for index, evidence in candidate_evidence.items()
            if covers_control_lines(evidence, control) and index not in candidate_ids
        }
        same_line_findings = [] if findings is None else [
            item for item in findings if item not in matching_findings and (
                covers_control_lines(item["evidence"], control)
                or same_line_candidates.intersection(item["candidates"])
            )
        ]
        expected_claims = int(control.expected)
        checks.append({
            "control": control.name,
            "claim_kind": claim_kind,
            "expected_claims": expected_claims,
            "matched_claims": claims,
            "structural_check_passed": claims == expected_claims,
            "manual_check": control.manual_check,
            "candidates": [{"index": index, **candidates[index]} for index in sorted(candidate_ids)],
            "findings": matching_findings,
            "same_line_candidates_for_review": [
                {"index": index, **candidates[index]} for index in sorted(same_line_candidates)
            ],
            "same_line_findings_for_review": same_line_findings,
        })

    unassigned_critical = [] if findings is None else [
        item for item in findings if item["priority"] == "критический" and item["id"] not in critical_ids
    ]
    unassigned_candidates = [
        {"index": index, **candidate}
        for index, candidate in candidates.items() if index not in matched_candidates
    ]
    # A conflict-only report for the complete BCC case cannot verify final priority.
    has_required_stage = findings is not None or fixture.startswith("payment")
    if fixture.startswith("payment"):
        short_output_matches = not (unassigned_candidates if findings is None else unassigned_critical)
    else:
        short_output_matches = True
    return {
        "fixture": fixture,
        "stage": "whole.findings" if findings is not None else "conflicts",
        "input_sha256": expected_hash,
        "structural_check_passed": (
            has_required_stage and short_output_matches
            and all(check["structural_check_passed"] for check in checks)
        ),
        "priority_available": findings is not None,
        "manual_review_required": bool(unassigned_critical) or any(
            check["same_line_candidates_for_review"] or check["same_line_findings_for_review"]
            for check in checks
        ),
        "checks": checks,
        "unassigned_critical_findings_for_review": unassigned_critical,
        "unassigned_candidates_for_review": unassigned_candidates,
        "scope": "Контрольные ссылки, категория и приоритет. Смысл находок проверяет человек.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", choices=FIXTURES)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        result = score_report(json.loads(args.report.read_text(encoding="utf-8")), args.fixture)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.exit(2, f"Ошибка проверки отчёта: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["structural_check_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
