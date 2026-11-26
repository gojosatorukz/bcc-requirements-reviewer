from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from reviewer.clarify import clarify
from reviewer.owner_decision import MATCH, MISMATCH, UNCONFIRMED
from reviewer.recheck import recheck, render_html


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runs/bcc-submission/review.json"
DIGEST = sha256(BASE.read_bytes()).hexdigest()
REVISED = ROOT / "eval/owner-decision/february.md"
ISSUE = json.loads(BASE.read_bytes())["whole"]["findings"][0]


def response(status):
    terms = ["Учитываем по дате проведения.", "Покупку учитываем только один раз."]
    raw = " ".join(terms)
    ready = status == "ready"
    data = {"kind": "specified" if ready else "partial", "evidence": ISSUE["evidence"], "retired": [],
            "commitments": [{"text": t, "support": [{"turn": 1, "quote": t}]} for t in terms],
            "gaps": [] if ready else ["Согласован ли граничный месяц"],
            "questions": [] if ready else ["Как учитываем покупку при смене месяца?"],
            "proposed_rules": [raw] if ready else []}
    with patch("reviewer.full.codex_provider.complete", return_value=(data, {})):
        return clarify(BASE, "D-01", [raw], "test", DIGEST)


def technical():
    evidence = [{"line": n, "quote": REVISED.read_text().splitlines()[n-1]} for n in (38, 64)]
    return {"checks": [{"id": "D-01", "status": "устранено", "reason": "Критерий и правило указывают на февраль.",
                        "revised_evidence": evidence, "diff_hunks": ["C-001"]}]}


def conditions(second=MATCH):
    source = technical()["checks"][0]["revised_evidence"]
    return {"conditions": [{"id": "D-01", "number": i, "reason": "Проверена строка условия.",
                            "status": s, "evidence": source} for i, s in ((1, MATCH), (2, second))]}


class ClarificationRecheckTests(TestCase):
    def test_partial_never_authorizes_closure_even_when_technical_check_passes(self):
        pending = response("needs_followup")
        with patch("reviewer.full.codex_provider.complete", return_value=(technical(), {})) as model:
            report = recheck(BASE, REVISED, "test", ["D-01"], clarifications=[pending], baseline_sha256=DIGEST)
        row, = report["checks"]
        self.assertEqual((row["status"], row["workflow_status"]), ("устранено", "открыто"))
        self.assertEqual(row["clarification"]["follow_up"], pending["follow_up"])
        self.assertIn("Замечание открыто", render_html(report))
        self.assertEqual(model.call_count, 1)
        self.assertNotIn(pending["answers"][0], model.call_args.args[0])

    def test_one_missing_or_conflicting_clause_blocks_closure(self):
        ready = response("ready")
        for second, workflow in ((MATCH, "закрыто"), (UNCONFIRMED, "открыто"), (MISMATCH, "открыто")):
            with self.subTest(second=second), patch("reviewer.full.codex_provider.complete",
                                                     side_effect=[(technical(), {}), (conditions(second), {})]) as model:
                report = recheck(BASE, REVISED, "test", ["D-01"], clarifications=[ready], baseline_sha256=DIGEST)
                row, = report["checks"]
                self.assertEqual(row["clarification"]["status"], second)
                self.assertEqual(row["workflow_status"], workflow)
                self.assertEqual(len(report["run"]["attempts"]), 2)
                self.assertNotIn(ready["answers"][0], model.call_args_list[0].args[0])
                self.assertIn(ready["rule"], model.call_args_list[1].args[0])
                self.assertIn("Замечание " + workflow, render_html(report))
                forged = deepcopy(report)
                forged["checks"][0]["workflow_status"] = "ошибка"
                with self.assertRaises(ValueError):
                    render_html(forged)

    def test_duplicate_or_missing_conditions_are_rejected(self):
        ready = response("ready")
        repeated = conditions()
        repeated["conditions"][1] = repeated["conditions"][0]
        with patch("reviewer.full.codex_provider.complete",
                   side_effect=[(technical(), {})] + [(repeated, {})]*3), self.assertRaisesRegex(ValueError, "условия"):
            recheck(BASE, REVISED, "test", ["D-01"], clarifications=[ready], baseline_sha256=DIGEST)

    def test_ambiguous_assignment_is_rejected_before_inference(self):
        ready = response("ready")
        with patch("reviewer.full.codex_provider.complete") as model:
            for records, choices, sha in (([ready, ready], [], DIGEST), ([ready], ["D-01=first"], DIGEST),
                                           ([ready], [], "a"*64), ([dict(ready, finding_id="D-02")], [], DIGEST)):
                with self.assertRaises(ValueError):
                    recheck(BASE, REVISED, "test", ["D-01"], decisions=choices, clarifications=records,
                            baseline_sha256=sha)
            model.assert_not_called()
