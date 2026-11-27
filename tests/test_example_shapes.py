from copy import deepcopy
import json
from pathlib import Path
import unittest

from reviewer.example_shapes import object_shapes
from reviewer.full_render import render_json_checks, validate_report
from reviewer.json_examples import inspect_examples
from reviewer.segments import segment


ROOT = Path(__file__).resolve().parents[1]


class ExampleShapeTests(unittest.TestCase):
    def test_anchored_observations_keep_duplicate_status_examples_separate(self):
        lines = ["## First API", "### HTTP 200", "```json", '{"ok":4}', "```",
                 "```json", '{"error":"bad"}', "```", "## Second API",
                 "### HTTP 200", "```json", '{"<note>":null,"meta":{"ignored":5}}', "```",
                 "```json", "[]", "```", "```json", "{", "```"]
        blocks = segment(lines)
        checks = inspect_examples(lines, blocks)
        shapes = object_shapes(lines, blocks, checks)
        self.assertEqual([r["start"] for r in shapes], [3, 6, 11])
        self.assertEqual([r["keys"] for r in shapes], [["ok"], ["error"], ["<note>", "meta"]])
        self.assertNotEqual(shapes[0]["section"], shapes[-1]["section"])
        html = render_json_checks(checks, dict(enumerate(lines, 1)), shapes)
        self.assertIn("&lt;note&gt;", html)
        self.assertNotIn("<note>", html)

    def test_validator_recomputes_contents_when_present_on_a_saved_review(self):
        old = json.loads((ROOT / "runs/bcc-executable/review.json").read_bytes())
        source = validate_report(old)
        lines = [source.get(i, "") for i in range(1, old["run"]["input_lines"] + 1)]
        shaped = deepcopy(old)
        shaped["example_shapes"] = object_shapes(lines, old["segments"], old["json_checks"])
        self.assertEqual(len(shaped["example_shapes"]), 6)
        validate_report(shaped)
        shaped["example_shapes"][-1]["keys"].append("invented")
        with self.assertRaisesRegex(ValueError, "Поля JSON"):
            validate_report(shaped)
