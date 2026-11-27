from copy import deepcopy
import unittest
from unittest.mock import patch

from reviewer import value_history, witness_vm
from reviewer.executable import VERSION, validate_saved


class ValueHistoryTest(unittest.TestCase):
    lines = {1: 'Palettes: mono, vivid, sepia.', 2: 'Show the current palette when saving.',
             3: 'Keep the original palette when saving.', 4: 'Start mono, select sepia, then save.',
             5: 'Select vivid and save without editing.'}

    def ref(self, n):
        return {"line": n, "quote": self.lines[n]}

    def setUp(self):
        self.witness = {s: {"source": self.ref(n)} for s, n in (("first", 2), ("second", 3))}
        self.rule = {"initial_side": "second", "options": [{"value": v, "source": self.ref(1)}
                     for v in ("mono", "vivid", "sepia")], "observable": "Палитра", "observation": "Сохранить",
                     "scenario": {"initial": "mono", "current": "sepia", "source": self.ref(4)},
                     "control": {"initial": "vivid", "current": "vivid", "source": self.ref(5)}}
        self.read = {"scenario": {"initial": "mono", "current": "sepia", "first": "sepia", "second": "mono"},
                     "control": {k: "vivid" for k in ("initial", "current", "first", "second")}}

    def build(self):
        def stage(name, prompt, payload, schema, verify, *args):
            if name.startswith("Правило"):
                reply = {"rules": [self.rule]}
            else:
                self.assertEqual(set(payload), {"source", "anchors", "examples", "vocabulary"})
                self.assertEqual(payload["examples"], {"scenario": self.ref(4), "control": self.ref(5)})
                self.assertEqual(payload["anchors"], {s: self.witness[s]["source"] for s in self.witness})
                reply = {"readings": [self.read]}
            verify(reply)
            return reply, []
        with patch.object(value_history, "call_stage", side_effect=stage):
            return value_history.build("D-01", self.witness, self.lines, {}, "model", None, "digest", [])

    def test_both_source_readings_replay_and_unsupported_inputs_stay_outside_scope(self):
        self.assertTrue(value_history.eligible(self.witness, self.lines))
        item = self.build()
        report = {"whole": {"findings": [{"id": "D-01", "category": "нарушение", "priority": "критический",
                                         "triage": {"witness": [self.witness]}}]},
                  "executables": {"version": VERSION, "items": [item]}}
        validate_saved(report, self.lines)
        self.assertEqual(item["result"]["scenario"]["final"], {"first": {"shown": "sepia"}, "second": {"shown": "mono"}})
        self.assertIsNone(item["result"]["control"]["first_difference"])
        other = deepcopy(item["program"]["scenario"])
        other["events"][0]["values"][0]["value"] = "vivid"
        with self.assertRaisesRegex(ValueError, "нет подтверждённого результата"):
            witness_vm.simulate(item["program"], self.lines, other, self.witness)
        for change in (lambda row: row.pop("history_proof"),
                       lambda row: row["history_proof"]["rule"].update(initial_side="first"),
                       lambda row: row["history_proof"]["readings"]["control"].update(first="sepia"),
                       lambda row: row["result"]["scenario"]["final"]["second"].update(shown="sepia")):
            copied = deepcopy(report)
            change(copied["executables"]["items"][0])
            with self.assertRaises(ValueError):
                validate_saved(copied, self.lines)

    def test_disagreement_on_inputs_or_outputs_and_invented_names_do_not_pass(self):
        for key in ("first", "initial"):
            old = self.read["scenario"][key]
            self.read["scenario"][key] = "vivid"
            self.assertIsNone(self.build())
            self.read["scenario"][key] = old
        self.rule["options"][2]["value"] = "purple"
        with self.assertRaises(ValueError):
            self.build()
