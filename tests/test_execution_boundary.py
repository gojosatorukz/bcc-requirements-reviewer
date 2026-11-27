from copy import deepcopy
import json
from pathlib import Path
from unittest import TestCase
from xml.etree import ElementTree

from reviewer.contract import source_lines
from reviewer.execution_boundary import MAX_REPLAYS_PER_EXAMPLE, scan_boundary
from reviewer.execution_view import render_executions
from reviewer.full_render import validate_report
from reviewer.witness_vm import execute


ROOT = Path(__file__).resolve().parents[1]
LINES = {1: "Действие зависит от положения шкалы.", 2: "Пример обработки показания."}


def instrument(guard, start=0, end=6):
    def case(value):
        return {"initial": [{"key": "marker", "value": 0}], "events": [
            {"kind": "read", "label": "Считывание", "values": [{"key": "dial", "value": value}]},
            {"kind": "reset", "label": "Сброс", "values": []}]}

    def side(line, when):
        return {"source": {"line": line, "quote": LINES[line]}, "rules": [
            {"event": "read", "when": when, "updates": [{"target": "marker", "expr": "marker + 1"}]},
            {"event": "reset", "when": "marker != 0", "updates": [{"target": "marker", "expr": "0"}]}]}

    program = {"inputs": [{"key": "dial", "label": "Шкала", "unit": "1"}],
               "states": [{"key": "marker", "label": "Индикатор", "unit": "1"}],
               "observe": ["marker"], "first": side(1, "False"), "second": side(2, guard),
               "control": case(start), "scenario": case(end)}
    return {"status": "executed", "finding_id": "D-01", "program": program, "result": execute(program, LINES)}


class ExecutionBoundaryTests(TestCase):
    def test_first_island_even_with_an_agreeing_midpoint_and_final_state(self):
        item = instrument("dial == 1 or dial >= 5")
        saved = deepcopy(item)
        result, used = scan_boundary(item, LINES)
        self.assertEqual((result["equal_input"], result["different_input"], used), (0, 1, 1))
        self.assertEqual((result["output_step"], result["equal_values"], result["different_values"]),
                         (1, [0, 0], [0, 1]))
        self.assertEqual(item["result"]["scenario"]["final"], {"first": {"marker": 0}, "second": {"marker": 0}})
        self.assertEqual(item, saved)

    def test_undefined_interior_stops_before_later_disagreements(self):
        item = instrument("dial >= 5 or 2 // (dial - 2) > 0")
        result, used = scan_boundary(item, LINES)
        self.assertEqual((result["status"], result["failed"], used), ("undefined", 2, 2))
        self.assertNotIn("different_input", result)

    def test_huge_ranges_and_exhausted_shared_budget_leave_verified_prefix(self):
        item = instrument("dial >= 1000000", end=1000000)
        for budget, expected in ((0, 0), (2, 2), (10**6, MAX_REPLAYS_PER_EXAMPLE)):
            with self.subTest(budget=budget):
                result, used = scan_boundary(item, LINES, budget)
                self.assertEqual((result["status"], result["last"], used), ("limited", expected, expected))
                self.assertNotIn("different_input", result)

    def test_unsupported_nominal_and_multiple_or_reordered_changes_are_skipped(self):
        self.assertEqual(scan_boundary({"status": "unsupported"}, LINES), (None, 0))
        report = json.loads((ROOT / "eval/executable/runs/named-state.json").read_bytes())
        item = report["executables"]["items"][0]
        self.assertEqual(scan_boundary(item, source_lines(report)), (None, 0))
        numeric = instrument("dial >= 5")
        for change in ("initial", "events", "label"):
            item = deepcopy(numeric)
            scenario = item["program"]["scenario"]
            if change == "initial":
                scenario["initial"][0]["value"] = 1
            elif change == "events":
                scenario["events"].reverse()
            else:
                scenario["events"][0]["label"] = "Другое событие"
            self.assertEqual(scan_boundary(item, LINES), (None, 0))

    def test_archived_official_report_shows_adjacent_inputs_in_descending_order(self):
        report = json.loads((ROOT / "runs/bcc-release/review.json").read_bytes())
        original = deepcopy(report)
        lines = validate_report(report)
        examples = {item["finding_id"]: item for item in report["executables"]["items"]}
        limit, used = scan_boundary(examples["D-02"], lines, budget=0)
        self.assertEqual((limit["step"], limit["equal_input"], limit["different_input"], used), (0, 10001, 10000, 0))
        panels = render_executions(report, lines)
        expected_rows = {
            "D-01": [["Совпадение · −1 мин", "0 ₸", "0 ₸"], ["Первое расхождение · −2 мин", "0 ₸", "4 000 ₸"]],
            "D-02": [["Совпадение · 10 001 ₸", "1 push", "1 push"], ["Первое расхождение · 10 000 ₸", "1 push", "2 push"]]}
        for key, expected in expected_rows.items():
            panel = ElementTree.fromstring(panels[key])
            table = panel.find('details[@class="execution-trace execution-boundary"]/table')
            self.assertEqual([[cell.text for cell in row] for row in table.findall("tbody/tr")], expected)
        self.assertNotIn("execution-boundary", panels["D-06"])
        self.assertEqual(report, original)
