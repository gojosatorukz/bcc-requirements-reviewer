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
