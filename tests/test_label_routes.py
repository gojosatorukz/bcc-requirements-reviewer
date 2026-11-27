from copy import deepcopy
import unittest
from unittest.mock import patch

from reviewer import label_routes, witness_vm
from reviewer.executable import validate_saved, VERSION


LINES = {1: 'При нагрузке менее 12 кВт включи «Один», иначе «Два».',
         2: 'При 12 кВт режим «Один».', 3: 'При 19 кВт режим «Два».'}


def ref(line):
    return {"line": line, "quote": LINES[line]}


def outcome(value, line):
    return {"value": value, "source": ref(line)}


class LabelRoutesTest(unittest.TestCase):
    def setUp(self):
        self.witness = {s: {"source": ref(line)} for s, line in (("first", 2), ("second", 1))}
        self.rule = {"rule_side": "second", "operator": "lt", "threshold": 12, "when_true": "Один",
                     "when_false": "Два", "input_label": "Нагрузка", "unit": "кВт",
                     "output_label": "Режим", "event": "Измерить нагрузку",
                     "scenario": {"input": 12, "output": "Один", "source": ref(2)},
                     "control": {"input": 19, "output": "Два", "source": ref(3)}}
        self.readings = {"scenario": {"first": outcome("Один", 2), "second": outcome("Два", 1)},
                         "control": {"first": outcome("Два", 3), "second": outcome("Два", 1)}}

    def build(self):
        stages = []
        def stage(name, prompt, data, schema, verify, *args):
            stages.append(data)
            if len(stages) == 1:
                value = {"decisions": [self.rule]}
            else:
                self.assertEqual(set(data), {"source", "anchors", "measurement", "unit", "action", "inputs"})
                self.assertEqual(data["inputs"], {"scenario": 12, "control": 19})
                value = {"readings": [self.readings]}
            verify(value)
            return value, []
        with patch.object(label_routes, "call_stage", side_effect=stage):
            return label_routes.build("D-01", self.witness, LINES, {}, "model", None, "digest", [])

    def test_replay_reverse_sides_and_refuse_unlisted_values(self):
        self.assertTrue(label_routes.eligible(self.witness, LINES))
        item = self.build()
        report = {"whole": {"findings": [{"id": "D-01", "category": "нарушение", "priority": "критический",
                                         "triage": {"witness": [self.witness]}}]},
                  "executables": {"version": VERSION, "items": [item]}}
        validate_saved(report, LINES)
        p = item["program"]
        self.assertEqual(witness_vm.simulate(p, LINES, p["scenario"])["final"],
                         {"first": {"result": "Один"}, "second": {"result": "Два"}})
        altered = deepcopy(p["control"])
        altered["events"][0]["values"][0]["value"] = 13
        with self.assertRaisesRegex(ValueError, "нет подтверждённого"):
            witness_vm.simulate(p, LINES, altered)
        for mutation in (lambda i: i["route_proof"]["readings"]["control"]["first"].update(value="Один"),
                         lambda i: i["result"]["scenario"]["final"]["first"].update(result="Два"),
                         lambda i: i.pop("route_proof"), lambda i: i["program"].pop("case_scope")):
            broken = deepcopy(report)
            mutation(broken["executables"]["items"][0])
            with self.assertRaises(ValueError):
                validate_saved(broken, LINES)

    def test_source_and_blind_prediction_must_agree(self):
        self.readings["scenario"]["second"]["value"] = "Один"
        self.assertIsNone(self.build())
        self.rule["control"]["input"] = 1
        with self.assertRaisesRegex(ValueError, "измерение"):
            label_routes.compile_rule(self.rule, self.witness, LINES)
        for wrong in ("+12", "-12", "112", "12.5", "0,12", "id12", "12e5"):
            self.assertFalse(label_routes.has_number(12, wrong), wrong)
