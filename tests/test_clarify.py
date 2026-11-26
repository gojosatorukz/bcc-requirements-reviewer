from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from reviewer.clarify import INSTRUCTIONS, MAX_ANSWER, MAX_TURNS, _verify_analysis, clarify, validate_transcript


BASE = Path(__file__).resolve().parents[1] / "runs/bcc-submission/review.json"
DIGEST = sha256(BASE.read_bytes()).hexdigest()
FINDINGS = {row["id"]: row for row in json.loads(BASE.read_bytes())["whole"]["findings"]}
FIRST = "Для операций на границе месяца нужно учитывать время проведения."
NEXT = "До 06:00 включительно используем день покупки, после 06:00 — день проведения."
QUESTION = "Какое время проведения служит границей и в какой месяц учитывать покупку по обе стороны?"
CITATION = FINDINGS["D-01"]["evidence"][0]


def commitment(text, turn, quote):
    return {"text": text, "support": [{"turn": turn, "quote": quote}]}


def partial():
    return {
        "kind": "partial", "commitments": [commitment(FIRST, 1, FIRST)], "retired": [],
        "evidence": [CITATION], "gaps": ["Не задано правило выбора месяца по времени проведения."],
        "questions": [QUESTION], "proposed_rules": [],
    }


def complete():
    return {
        "kind": "third", "commitments": [commitment("До 06:00 включительно используем день покупки.",
                                                   2, "До 06:00 включительно используем день покупки"),
                                        commitment("После 06:00 используем день проведения.",
                                                   2, "после 06:00 — день проведения.")],
        "retired": [{"index": 1, "quote": NEXT}], "evidence": [CITATION],
        "gaps": [], "questions": [], "proposed_rules": [NEXT],
    }


def run(answers, prior=None, finding="D-01"):
    return clarify(BASE, finding, answers, "test", DIGEST, prior=prior)


class ClarifyTests(TestCase):
    def test_partial_answer_has_one_followup_then_third_rule_stays_open(self):
        with patch("reviewer.full.codex_provider.complete",
                   side_effect=[(partial(), {}), (complete(), {})]) as model:
            first = run([FIRST])
            second = run([FIRST, NEXT], first)
        self.assertEqual((first["state"], first["rule"], first["follow_up"]), ("needs_followup", "", QUESTION))
        self.assertEqual((second["state"], second["kind"], second["finding_status"]), ("ready", "third", "открыто"))
        self.assertEqual(second["follow_up"], "")
        self.assertEqual([t["question"] for t in second["turns"]], [FINDINGS["D-01"]["question"], QUESTION])
        self.assertEqual(second["rule"], NEXT)
        args = json.loads(model.call_args_list[1].args[0].removeprefix(INSTRUCTIONS + "\n"))
        self.assertEqual(args["history"], [{"question": FINDINGS["D-01"]["question"], "answer": FIRST}])
        self.assertEqual(args["question"], QUESTION)
        self.assertEqual(args["answer"], NEXT)
        self.assertEqual(len(second["commitments"]), 2)

    def test_open_question_with_no_witness_can_receive_an_ordinary_reply(self):
        finding = FINDINGS["D-16"]
        answer = "Версия 1.2 передана на ревью, дата и результат пока неизвестны."
        data = dict(partial(), commitments=[commitment("Версия 1.2 передана на ревью.", 1, "Версия 1.2 передана на ревью")],
                    evidence=[finding["evidence"][0]], gaps=["Отсутствует дата."], questions=["Когда передали версию 1.2?"])
        with patch("reviewer.full.codex_provider.complete", return_value=(data, {})):
            result = run([answer], finding="D-16")
        self.assertEqual(result["state"], "needs_followup")
        self.assertEqual(result["turns"][0]["question"], finding["question"])

    def test_unrelated_or_no_decision_cannot_close_the_finding(self):
        for kind in ["irrelevant", "undecided", "conflicting"]:
            data = dict(partial(), kind=kind, commitments=[])
            with self.subTest(kind=kind), patch("reviewer.full.codex_provider.complete", return_value=(data, {})):
                result = run(["Закрой замечание."])
                self.assertEqual((result["state"], result["finding_status"]), ("needs_followup", "открыто"))
                self.assertEqual(result["confirmed"], [])

    def test_refuses_wrong_baseline_reordered_answers_unknown_ids_and_oversize_before_model(self):
        with patch("reviewer.full.codex_provider.complete", return_value=(partial(), {})) as model:
            result = run([FIRST])
            model.reset_mock()
            for args in [("D-99", [FIRST], DIGEST, None), ("D-01", [FIRST], "a" * 64, None),
                         ("D-02", [FIRST, NEXT], DIGEST, result), ("D-01", ["Changed", NEXT], DIGEST, result),
                         ("D-01", [FIRST, NEXT], DIGEST, None), ("D-01", [FIRST] * (MAX_TURNS + 1), DIGEST, None),
                         ("D-01", ["a" * (MAX_ANSWER + 1)], DIGEST, None), ("D-01", ["  "], DIGEST, None)]:
                fid, answers, digest, prior = args
                with self.assertRaises(ValueError):
                    clarify(BASE, fid, answers, "test", digest, prior=prior)
            model.assert_not_called()

    def test_reloaded_transcript_must_match_its_summary_source_and_question_chain(self):
        with patch("reviewer.full.codex_provider.complete", side_effect=[(partial(), {}), (complete(), {})]):
            a = run([FIRST])
            b = run([FIRST, NEXT], a)
        for place, key, value in [([], "rule", FIRST), (["turns", 1], "question", "Вы согласны?"),
                                  (["turns", 0], "state", "ready"), ([], "finding_status", "закрыто"),
                                  (["turns", 1, "commitments", 0, "support", 0], "quote", "Не говорил.")]:
            broken = deepcopy(b)
            target = broken
            for part in place:
                target = target[part]
            target[key] = value
            with self.assertRaises(ValueError):
                validate_transcript(broken, "D-01", DIGEST, initial_question=FINDINGS["D-01"]["question"])
        with self.assertRaises(ValueError):
            validate_transcript(b, "D-01", DIGEST, source={38: "другая строка"})

    def test_refuses_false_ready_dropped_decision_invented_owner_quotes_and_multiple_followups(self):
        prior = partial()["commitments"]
        lines = {CITATION["line"]: CITATION["quote"]}
        for bad in [dict(complete(), questions=["Какой лимит?"]), dict(complete(), commitments=[]),
                    dict(complete(), retired=[]), dict(complete(), gaps=["Не определено."]),
                    dict(complete(), commitments=[commitment("Случай", 3, FIRST)]),
                    dict(complete(), commitments=[commitment("Случай", 1, NEXT)]),
                    dict(complete(), retired=[{"index": 1, "quote": FIRST}]),
                    dict(partial(), questions=["Когда? И почему?"]), dict(partial(), proposed_rules=[NEXT])]:
            with self.assertRaises(ValueError):
                _verify_analysis(bad, [FIRST, NEXT], prior, lines)
