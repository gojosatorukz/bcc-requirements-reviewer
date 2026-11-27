from copy import deepcopy
import unittest

from reviewer import response_fields
from reviewer.example_shapes import object_shapes
from reviewer.full_render import reviewed_field_cells
from reviewer.json_examples import inspect_examples
from reviewer.segments import segment


class ResponseFieldReviewTests(unittest.TestCase):
    def setUp(self):
        self.lines = [
            "# Service", "## PATCH /drawings", "Routing field is documented without a presence rule.",
            "### HTTP 202", "```json", '{"stampId":"s1"}', "```", "### HTTP 409",
            "```json", '{"stampId":"bad","note":"busy"}', "```", "### HTTP 410", "**404**",
            "```json", '{"note":"absent"}', "```",
            "## GET /drawings", "### HTTP 409", "```json", '{"note":"busy"}', "```",
        ]
        blocks = segment(self.lines)
        shapes = object_shapes(self.lines, blocks, inspect_examples(self.lines, blocks))
        self.source = dict(enumerate(self.lines, 1))
        self.groups = response_fields.response_groups(self.source, shapes, blocks)
        self.choices = response_fields.candidates(self.groups)
        self.choice = self.choices[0]
        self.question = "Должно ли поле stampId присутствовать в ответах HTTP 409 и 404?"
        self.response = {"assessments": [
            {"candidate": self.choice["id"], "rulings": [{"policy": "unspecified", "examples": [9, 14],
              "evidence": [{"line": 3, "quote": self.lines[2]}], "basis": "Область поля не указана."}],
             "questions": [self.question]}]}

    def test_both_presence_states_and_direct_caption_are_scoped_to_the_operation(self):
        self.assertEqual(self.choice, {"id": 1, "operation": 2, "field": "stampId",
                                      "present": [5, 9], "errors": {9: 409, 14: 404}})
        self.assertEqual([e["start"] for e in self.groups[17]], [19])
        record = response_fields.describe([self.choice])[0]
        self.assertEqual(record["errors"], [{"example": 9, "http_status": 409, "present": True},
                                            {"example": 14, "http_status": 404, "present": False}])

    def test_ruling_needs_every_outcome_and_a_source_grounded_owner_question(self):
        response_fields.verify(self.response, [self.choice], self.source)
        for name, change in {
            "missing status": lambda row: row["questions"].__setitem__(0, "Нужен ли stampId для 409?"),
            "unknown citation": lambda row: row["rulings"][0]["evidence"][0].update(line=8),
            "another operation": lambda row: row["rulings"][0].update(examples=[9, 19]),
            "repeated outcome": lambda row: row["rulings"][0].update(examples=[9, 14, 14]),
            "question about a resolved rule": lambda row: row["rulings"][0].update(policy="required"),
        }.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                broken = deepcopy(self.response)
                change(broken["assessments"][0])
                response_fields.verify(broken, [self.choice], self.source)

    def test_question_must_survive_in_the_actual_finding_within_its_own_operation(self):
        finding = {"question": self.question, "evidence": [{"line": 15, "quote": self.lines[14]}]}
        response_fields.check_questions(self.response, [self.choice], [finding], self.groups)
        for invalid in [dict(finding, question="На каких ошибках нужно поле stampId?"),
                        dict(finding, evidence=[{"line": 20, "quote": self.lines[19]}])]:
            with self.assertRaises(ValueError):
                response_fields.check_questions(self.response, [self.choice], [invalid], self.groups)

    def test_mixed_policy_does_not_require_a_question_for_the_defined_outcome(self):
        row = self.response["assessments"][0]
        row["rulings"][0]["examples"] = [14]
        row["rulings"].append({"policy": "required", "examples": [9], "basis": "Правило 409.",
                               "evidence": [{"line": 3, "quote": self.lines[2]}]})
        row["questions"] = ["Обязателен ли stampId при HTTP 404?"]
        response_fields.verify(self.response, [self.choice], self.source)
        row["rulings"][0]["policy"] = "excluded"
        row["questions"] = []
        response_fields.verify(self.response, [self.choice], self.source)
        response_fields.check_questions(self.response, [self.choice], [], self.groups)
        section = {"candidates": response_fields.describe([self.choice]), **self.response}
        cell = reviewed_field_cells(section)[2, "stampId"]
        self.assertIn("Исключено для HTTP 404", cell)
        self.assertIn("Обязательно для HTTP 409", cell)
        self.assertNotIn("?", cell)
