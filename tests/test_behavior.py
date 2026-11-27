import unittest

from reviewer import behavior
from reviewer.segments import segment


class BehaviorTests(unittest.TestCase):
    def read(self, lines):
        source = dict(enumerate(lines, 1))
        payload, anchors = behavior.reading(segment(lines), source)
        return source, payload, anchors

    def test_focused_pass_keeps_authored_table_headings_and_numbered_sources(self):
        lines = ["# Notes", "## Functional requirements", "A can be archived.",
                 "## Acceptance tests", "| Given | When | Expected result |", "| --- | --- | --- |",
                 "| A | Archive | A stays active |", "## Changelog", "Changed the font."]
        _, payload, _ = self.read(lines)
        self.assertEqual(payload['rules'], [3])
        self.assertEqual(payload['examples'], [7])
        self.assertEqual(set(payload['source']), {3, 5, 7})

    def test_unknown_headings_and_long_rows_preserve_exact_original_quotes(self):
        source, payload, anchors = self.read(["# Книга", "Часть правила. " * 45, "Итог изменён."])
        rule, other = payload["rules"][0], payload["rules"][-1]
        candidate = {"title": "Регистрация", "scenario": "У записи меняют статус", "owner": "Редактор",
                     "question": "Какой статус нужен?", "agreement_control": "Без смены статус прежний.",
                     "first": {"anchor":rule,"outcome":"Старая запись"}, "second":{"anchor":other,"outcome":"Новая запись"}}
        item, = behavior.expand({"conflicts":[candidate]}, anchors, source)["conflicts"]
        self.assertEqual((item["first"]["source"]["line"], item["second"]["source"]["line"]), (2, 3))
        self.assertIn(item["first"]["source"]["quote"], source[2])
        with self.assertRaisesRegex(ValueError, "anchor"):
            behavior.expand({"conflicts":[dict(candidate, first={"anchor":-1,"outcome":"Старая запись"})]}, anchors, source)
        self.assertEqual(behavior.expand({"conflicts":[]}, anchors, source), {"conflicts":[]})

    def test_prediction_never_sees_expected_output_including_reordered_columns(self):
        lines = ["## Functional requirements", "Publish the letter.",
                 "## Acceptance", "| Given | Expected outcome | When |", "|---|---|---|",
                 r"| draft A\|B | hidden-original | confirm |"]
        source, reading, anchors = self.read(lines)
        inputs, printed = behavior.table_cases(segment(lines), source, reading, anchors)
        self.assertNotIn("hidden-original", str(inputs))
        self.assertEqual(printed, {6:"hidden-original"})
        self.assertEqual(inputs["cases"][0]["inputs"], [{"column":"Given", "value":r"draft A\|B"},
                                                       {"column":"When","value":"confirm"}])
        prediction = {"example":6, "bases":[2], "predicted":"Published"}
        behavior.check_predictions({"predictions":[prediction]}, inputs)
        with self.assertRaises(ValueError):
            behavior.check_predictions({"predictions":[]}, inputs)
        with self.assertRaises(ValueError):
            behavior.check_predictions({"predictions":[dict(prediction,bases=[6])]}, inputs)
        disagreement = {"checks":[{"example":6,"verdict":"disagrees","predicted_value":"A","printed_value":"B"}],"conflicts":[]}
        with self.assertRaises(ValueError):
            behavior.check_comparisons(disagreement, [prediction], anchors, {2})
        disagreement["conflicts"] = [{"first":{"anchor":6}, "second":{"anchor":2}}]
        behavior.check_comparisons(disagreement, [prediction], anchors, {2})

    def test_acceptance_document_title_does_not_hide_normative_prose_or_reveal_table_answers(self):
        lines = ["# Acceptance of prints", "At most two signed copies per box.", "Send one additional signed copy.",
                 "| Given | When | Expected result |", "| --- | --- | --- |", "| Full | Send | Hidden output |"]
        source, reading, anchors = self.read(lines)
        self.assertEqual(reading["rules"], [2, 3])
        inputs, _ = behavior.table_cases(segment(lines), source, reading, anchors)
        self.assertNotIn("Hidden output", str(inputs))

    def test_a_global_claim_about_an_agreeing_example_requires_two_distinct_observable_values(self):
        refs = [{"line": 8, "quote": "Retain the uploader."}, {"line": 26, "quote": "The uploader still owns it."}]
        witness = {"scenario": "Upload and transfer.", "first": {"source": refs[0], "outcome": "Owned by A"},
                   "second": {"source": refs[1], "outcome": "A retains ownership"}}
        f = {"triage": {"witness": [witness]}, "evidence": refs, "candidates": [1], "requirements": ["R-2"],
             "question": "Who owns it?"}
        gate = dict(status="заблокировано", rationale="Owner unknown.", evidence=refs)
        whole = {"findings": [f], "reconciled": [], "development": dict(gate), "testing": dict(gate)}
        candidate = {"first": witness["first"], "second": witness["second"]}
        agreements = [{"example": 26, "rule_lines": [8]}]
        disputed = behavior.disputed_examples(whole, agreements, [candidate])
        self.assertNotIn("claimed_outcomes", disputed[0])
        source = {r["line"]: r["quote"] for r in refs}
        ruling = {"finding": 0, "observable": "owner", "covered": True, "values": ["A"],
                  "rule_value": 1, "example_value": 1, "rule_quote": refs[0]["quote"],
                  "example_quote": refs[1]["quote"], "explanation": "Both retain A."}
        def decide(**changes):
            return behavior.agreed_dispositions({"comparisons": [dict(ruling, **changes)]}, disputed, source)
        self.assertEqual(decide(covered=False), {})
        self.assertEqual(decide(values=["A", "B"], example_value=2), {})
        with self.assertRaises(ValueError):
            decide(example_value=2)
        with self.assertRaisesRegex(ValueError, "цитата"):
            decide(rule_quote=refs[1]["quote"])
        f["candidates"].append(2)
        unrelated = {"first": witness["first"], "second": {"source": {"line": 30}}}
        self.assertEqual(behavior.disputed_examples(whole, agreements, [candidate, unrelated]), [])
        f["candidates"].pop()
        behavior.resolve_agreements(whole, decide())
        self.assertEqual(whole["findings"], [])
        self.assertEqual(whole["reconciled"][0]["evidence"], refs)

    def test_recorded_museum_disagreement_reaches_the_independent_check(self):
        import json
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "eval/gemini/behavior"
        full = json.loads((root / "museum-aligned-release.json").read_bytes())
        stages = json.loads((root / "museum-aligned-stage-outputs.json").read_bytes())
        for key in ("input_sha256", "engine_sha256", "model"):
            self.assertEqual(stages[key], full["run"][key])
        basis = {p["example"]: p["bases"] for p in stages["Прогноз по правилам"]["predictions"]}
        agreed = [dict(c, rule_lines=basis[c["example"]]) for c in stages["Сверка примеров"]["checks"]
                  if c["verdict"] == "agrees"]
        review, = behavior.disputed_examples(full["whole"], agreed, full["conflicts"])
        self.assertEqual((review["rule_line"], review["example_line"]), (5, 15))

    def test_production_reconciliation_replays_recorded_decisions_and_keeps_true_conflicts(self):
        from eval.gemini.behavior.precision import CASES, ROOT, replay
        for case in CASES:
            with self.subTest(case=case):
                result = replay(case, ROOT / "precision")
                if case == "museum-aligned":
                    self.assertEqual(result["after"], [])
                    self.assertEqual(result["readiness"], ["можно начинать", "можно начинать"])
                else:
                    self.assertEqual(result["after"], result["before"])

    def test_addressed_contract_keeps_same_status_examples_independent(self):
        from reviewer import response_fields
        c = {"id":7, "field":"receipt", "errors":{40:409, 46:409, 58:422}}
        row = {"outcomes":{"example_40":"excluded","example_46":"unspecified","example_58":"unspecified"},
               "reasons":[{"policy":"excluded","basis":"Точный контракт","evidence":[{"line":40,"quote":"A"}]},
                          {"policy":"unspecified","basis":"Нужен ответ","evidence":[{"line":46,"quote":"B"}]}]}
        answer = {"field_7":row}
        from reviewer.contract import validate_shape
        validate_shape(answer, response_fields.addressed_schema([c]), "test")
        restored = response_fields.from_addressed(answer, [c])
        response_fields.verify(restored, [c], {40:"A",46:"B",58:"C"})
        q, = restored["assessments"][0]["questions"]
        self.assertIn("receipt", q)
        self.assertIn("46", q)
        self.assertNotIn("строк 40", q)

    def test_presence_rule_for_another_field_does_not_close_a_field_dictionary(self):
        from reviewer import response_fields
        candidate = {"id": 1, "field": "receipt", "errors": {33: 409}}
        row = {"outcomes": {"example_33": "excluded"}, "reasons": [
            {"policy": "excluded", "basis": "Description", "evidence": [
                {"line": 4, "quote": "| receipt | string | PASS / HOLD |"},
                {"line": 5, "quote": "| detail | string | when validation fails |"}]}]}
        ruling, = response_fields.from_addressed({"field_1": row}, [candidate])["assessments"]
        self.assertIn("409", ruling["questions"][0])
        row["reasons"][0]["evidence"][1]["quote"] = "| Error schema | Only field: detail |"
        ruling, = response_fields.from_addressed({"field_1": row}, [candidate])["assessments"]
        self.assertEqual(ruling["questions"], [])

    def test_a_saturated_count_is_evaluated_with_an_independent_rule(self):
        from reviewer import count_limits
        rules = {4:"There can be at most 3 current leases per bin.", 8:"An extra lease is issued upon arrival."}
        cases = count_limits.experiments(rules)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["initial"], 3)
        self.assertNotIn("at most", cases[0]["observation"])
        self.assertEqual(count_limits.experiments({4:"At most 3,000 parts."}), [])
        self.assertEqual(count_limits.experiments({4:"Не более двух тысяч деталей."}), [])
        refs = {n:{"line":n,"quote":t} for n,t in rules.items()}
        effect = {"other":8,"added":1,"observable":"leases","setup":"Three active leases.",
                  "action":"An arrival is registered.", "triggering_event":"Another lease is issued.",
                  "owner":"Lead","question":"May a fourth be issued?","agreement_control":"Start with two."}
        report = count_limits.review({"probes":[{"case":1,"effects":[effect]}]}, cases, {8:rules[8]}, refs)
        pair, = report["conflicts"]
        self.assertIn("3 + 1 = 4", pair["second"]["outcome"])
        self.assertEqual(count_limits.review({"probes":[{"case":1,"effects":[]}]}, cases, {}, refs), {"conflicts":[]})

    def test_inferred_precedence_cannot_retire_a_bound_but_an_explicit_exception_can(self):
        from reviewer import count_limits
        row = {"reason":"У частного действия есть приоритет.", "evidence":[{"quote":"Produce one record."}]}
        with self.assertRaisesRegex(ValueError,"явную отмену"):
            count_limits.check_precedence(row, {2})
        row["evidence"].append({"quote":"This exception overrides the per-order limit."})
        count_limits.check_precedence(row,{2})
        count_limits.check_precedence({"reason":"Правила относятся к разным заказам.", "evidence":[]},{2})

    def test_final_summary_cannot_retire_an_independently_confirmed_numeric_trace(self):
        from reviewer import count_limits
        source = {4: "At most two signed copies per box.", 9: "Send an additional copy to the same box."}
        anchors = {n: {"line": n, "quote": s} for n, s in source.items()}
        checked = count_limits.projected_pairs(source, count_limits.experiments(source), [], anchors)
        trace = dict(pair=1, applicability="same_scope", population="current_objects",
                     retained=2, removal_quote=None, required_additions=1, explanation="2 + 1",
                     exception_kind="none", exception_quote=None, addition_quote="Send an additional copy",
                     scenario="Two copies exist, a third is sent.", metric="copies in the box",
                     title="Copy limit", owner="Publisher", question="May the third copy be added?",
                     agreement_control="With one copy there is space.")
        def certify(**changes):
            from reviewer.contract import validate_shape
            output = {"traces": [dict(trace, **changes)]}
            validate_shape(output, count_limits.AUDIT, "count")
            return count_limits.certified(output, checked, source, anchors)
        proof, = certify()
        self.assertEqual(certify(required_additions=0, addition_quote=None), [])
        with self.assertRaisesRegex(ValueError, "Для убыли"):
            certify(retained=1)
        source[12] = "One earlier copy is withdrawn before a new one is sent."
        self.assertEqual(certify(retained=1, removal_quote=source[12]), [])
        historical, = certify(population="occurred_events", retained=0, exception_kind="explicit_override",
                              exception_quote="Send an additional copy")
        self.assertIn("2 + 1 = 3", historical["second"]["outcome"])
        self.assertEqual(certify(applicability="other_scope"), [])
        self.assertEqual(certify(addition_quote="the same box"), [])
        with self.assertRaisesRegex(ValueError, "исключения в цифрах"):
            certify(exception_quote=source[9], exception_kind="blocked")
        with self.assertRaisesRegex(ValueError, "дословным"):
            certify(exception_kind="explicit_override", exception_quote="unless this is a reprint")
        gate = dict(status="можно начинать", rationale="Ready.", evidence=[anchors[4]])
        summary = dict(findings=[], reconciled=[dict(requirements=["R-004"], candidates=[1],
                       evidence=list(anchors.values()), reason="The rules supplement each other.")],
                       development=dict(gate), testing=dict(gate))
        registry = [{"id": "R-004", "evidence": anchors[4]}]
        count_limits.apply_proofs(summary, [1], [proof], registry)
        finding, = summary["findings"]
        self.assertEqual((finding["priority"], finding["requirements"], finding["candidates"]),
                         ("критический", ["R-004"], [1]))
        self.assertEqual(summary["reconciled"], [])
        self.assertIn("2 + 1 = 3", finding["impact"])
        self.assertIn("максимум 2 или 3?", finding["question"])
        self.assertEqual(summary["testing"]["status"], "можно частично")

    def test_preserving_existing_badges_does_not_witness_creation_above_the_limit(self):
        from copy import deepcopy
        from reviewer import count_limits
        checks = [{"limit_line": 8, "action_line": 5, "initial_limit": 2, "counted_object": "badges",
                   "applicability": "same_scope", "added": 0, "retained": 2}]
        refs = [{"line": 5, "quote": "Existing badges remain valid."}, {"line": 8, "quote": "At most two badges."}]
        w = {"scenario": "Three badges already exist. Open the account.",
             "first": {"source": refs[0], "outcome": "Three badges remain valid."},
             "second": {"source": refs[1], "outcome": "Two badges at most."}}
        f = {"triage": {"witness": [w]}, "evidence": refs, "candidates": [1], "requirements": ["R-4"]}
        gate = {"status": "заблокировано", "rationale": "Count conflict.", "evidence": refs}
        summary = dict(findings=[f], reconciled=[], development=dict(gate), testing=dict(gate))
        prior = deepcopy(summary)
        count_limits.retire_unreachable_counts(summary, checks)
        self.assertEqual(summary["findings"], [])
        self.assertEqual(summary["reconciled"][0]["candidates"], [1])
        legitimate = deepcopy(prior)
        count_limits.retire_unreachable_counts(legitimate, [dict(checks[0], added=1)])
        self.assertEqual(legitimate, prior)
        w = prior["findings"][0]["triage"]["witness"][0]
        w["scenario"] = "Two badges exist. Then another is issued."
        count_limits.retire_unreachable_counts(prior, checks)
        self.assertEqual(len(prior["findings"]), 1)
