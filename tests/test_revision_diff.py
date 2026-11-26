from random import Random
import unittest

from reviewer.recheck import RESOLVED, check_decisions, make_diff


class RevisionDiffTest(unittest.TestCase):
    def test_shifted_formatted_prose_is_equal(self):
        old = {2: '  Product says "same  words".', 4: "Review  this."}
        new = {7: 'Product says "same words".', 9: "\tReview this."}
        self.assertEqual(make_diff(old, new), [])
        self.assertEqual(make_diff(old, old), [])

    def test_whitespace_inside_json_strings_changes_document(self):
        for old, new, line in (
            ({1: '{"message": "two  spaces"}'}, {9: '{"message":"two spaces"}'}, 9),
            ({1: 'Example `{"message":"a  b"}`'}, {7: 'Example  `{"message": "a b"}`'}, 7),
            ({1: "```json", 2: '[', 3: ' "a  b"', 4: ']', 5: "```"},
             {9: "```json", 10: '[', 11: ' "a b"', 12: ']', 13: "```"}, 11),
            ({1: "[", 2: '"raw  value"', 3: "]"}, {8: "[", 11: '"raw value"', 12: "]"}, 11),
            ({1: r'{"message":"escaped \"quotes\"  here"}'}, {2: r'{"message":"escaped \"quotes\" here"}'}, 2),
            ({1: '"key  name": "value",'}, {2: '"key name": "value",'}, 2),
        ):
            with self.subTest(new=new):
                hunks = make_diff(old, new)
                self.assertEqual(len(hunks), 1)
                self.assertEqual(hunks[0]["operation"], "replace")
                self.assertEqual(hunks[0]["after"], [{"line": line, "text": new[line]}])

    def test_json_layout_whitespace_outside_quoted_tokens_is_ignored(self):
        old = {1: "```JSON", 2: '{"value" : "two  spaces", "array" : [1, 2]}', 3: "```"}
        new = {8: "```JSON", 10: '    {"value":"two  spaces","array":[1,2]}', 12: "```"}
        self.assertEqual(make_diff(old, new), [])

    def test_only_json_strings_in_json_scope(self):
        old = {1: "```json", 2: '"a  b"', 3: "```", 4: '"prose  after"'}
        new = {1: "```json", 2: '"a b"', 3: "```", 4: '"prose after"'}
        self.assertEqual([row["line"] for hunk in make_diff(old, new) for row in hunk["after"]], [2])

    def test_changed_json_string_is_accepted_as_evidence(self):
        old = {1: '{"value":"a  b"}'}
        new = {2: '{"value": "a b"}'}
        diff = make_diff(old, new)
        result = {"checks": [{"id": "D-01", "status": RESOLVED, "reason": "Исправлено значение.",
                             "revised_evidence": [{"line": 2, "quote": '"a b"'}], "diff_hunks": ["C-001"]}]}
        check_decisions(result, [{"id": "D-01", "candidates": []}], old, new, diff)

    def test_edge_trimming_preserves_exact_source_ranges_for_edits(self):
        rng = Random(994)
        for _ in range(40):
            prefix = ["repeated boundary"] * rng.randrange(0, 30)
            suffix = ["repeated boundary"] * rng.randrange(0, 30)
            for removals, additions in ((1, 2), (0, 2), (2, 0)):
                before = {2 * i + 1: text for i, text in enumerate(
                    prefix + ["old edit"] * removals + suffix)}
                after = {3 * i + 6: text for i, text in enumerate(
                    prefix + ["new edit"] * additions + suffix)}
                hunks = make_diff(before, after)
                self.assertEqual(len(hunks), 1)
                edit = hunks[0]
                self.assertEqual(edit["id"], "C-001")
                self.assertEqual(edit["operation"], {(1, 2): "replace", (0, 2): "insert", (2, 0): "delete"}
                                 [removals, additions])
                self.assertEqual(edit["before"], [{"line": 2 * i + 1, "text": "old edit"}
                                                  for i in range(len(prefix), len(prefix) + removals)])
                self.assertEqual(edit["after"], [{"line": 3 * i + 6, "text": "new edit"}
                                                 for i in range(len(prefix), len(prefix) + additions)])
