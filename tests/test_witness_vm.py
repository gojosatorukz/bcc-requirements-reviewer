from copy import deepcopy
import json
import random
import unittest

from reviewer.witness_vm import execute, simulate


PURCHASE = "Месяц определяется по дате совершения покупки."
POSTING = "Начислить расходы в месяц проведения покупки."
LINES = {38: PURCHASE, 64: POSTING}
SOURCES = {"first": {"line": 38, "quote": PURCHASE}, "second": {"line": 64, "quote": POSTING}}
WITNESS = {side: {"source": cite, "outcome": "Модель эту строку не получает."} for side, cite in SOURCES.items()}


def fields(**items):
    return [{"key": key, "label": key, "unit": unit} for key, unit in items.items()]


def values(**items):
    return [{"key": key, "value": value} for key, value in items.items()]


def event(kind, **items):
    return {"kind": kind, "label": kind, "values": values(**items)}


def update(kind, target, expression, guard="True"):
    return {"event": kind, "when": guard, "updates": [{"target": target, "expr": expression}]}


def ledgers(amount=4000, january=0, february=0):
    def case(bought, posted):
        return {"initial": values(jan=january, feb=february), "events": [
            event("settled", amount=amount, bought=bought, posted=posted)]}

    def side(key, month):
        return {"source": SOURCES[key], "rules": [
            update("settled", account, f"{account} + amount", f"{month} == {n}")
            for n, account in enumerate(("jan", "feb"), 1)]}
    return {"states": fields(jan="₸", feb="₸"), "inputs": fields(amount="₸", bought="месяц", posted="месяц"),
            "observe": ["jan", "feb"], "scenario": case(1, 2), "control": case(2, 2),
            "first": side("first", "bought"), "second": side("second", "posted")}


def threshold_program():
    def case(old_pushes):
        return {"initial": values(spent=10000, limit=10000, count=old_pushes, enabled=1),
                "events": [event("change", new_limit=15000), event("settle", amount=5000)]}

    def side(name, guard):
        return {"source": SOURCES[name], "rules": [
            update("change", "limit", "new_limit"), update("settle", "spent", "spent + amount"),
            update("settle", "count", "count + 1",
                   "enabled == 1 and spent < limit <= spent + amount" + guard)]}
    return {"states": fields(spent="₸", limit="₸", count="шт", enabled="1"),
            "inputs": fields(amount="₸", new_limit="₸"), "observe": ["spent", "count"],
            "scenario": case(1), "control": case(0),
            "first": side("first", " and count == 0"), "second": side("second", "")}


class ExecutableWitnessTests(unittest.TestCase):
    def test_two_ledger_allocation_is_computed_from_event_not_textual_answers(self):
        witness = ledgers()
        before = deepcopy(witness)
        trace = execute(witness, LINES, WITNESS)
        self.assertEqual(trace["scenario"]["first_difference"],
                         {"step": 1, "field": "jan", "first": 4000, "second": 0})
        step = trace["scenario"]["steps"][0]
        self.assertEqual(step["different"], ["jan", "feb"])
        self.assertEqual(step["first"], {"jan": 4000, "feb": 0})
        self.assertEqual(step["second"], {"jan": 0, "feb": 4000})
        self.assertEqual(trace["control"]["final"]["first"], {"jan": 0, "feb": 4000})
        self.assertIsNone(trace["control"]["first_difference"])
        self.assertEqual(witness, before)
        self.assertEqual(execute(json.loads(json.dumps(witness)), LINES, WITNESS), trace)

    def test_random_amounts_and_balances_follow_money_conservation(self):
        rng = random.Random(27031)
        for _ in range(90):
            amount, jan, feb = (rng.randrange(1, 1000000) for _ in range(3))
            trace = execute(ledgers(amount, jan, feb), LINES)
            for name in ["scenario", "control"]:
                for result in trace[name]["final"].values():
                    self.assertEqual(sum(result.values()), jan + feb + amount)
            self.assertEqual(trace["scenario"]["final"]["second"], {"jan": jan, "feb": feb + amount})
            self.assertEqual(trace["scenario"]["final"]["first"], {"jan": jan + amount, "feb": feb})

    def test_second_event_and_output_only_use_prior_state(self):
        trace = execute(threshold_program(), LINES)
        self.assertEqual(trace["scenario"]["steps"][0]["different"], [])
        self.assertEqual(trace["scenario"]["first_difference"], {"step": 2, "field": "count", "first": 1, "second": 2})
        self.assertEqual(trace["control"]["final"],
                         {"first": {"spent": 15000, "count": 1}, "second": {"spent": 15000, "count": 1}})
        swapped = threshold_program()
        swapped["first"]["rules"].reverse()
        swapped["second"]["rules"].reverse()
        self.assertEqual(execute(swapped, LINES), trace)

    def test_same_program_replays_both_sides_of_boundary_and_on_boundary(self):
        program = threshold_program()
        for amount, expected in ((4999, None), (5000, 2), (5001, 2)):
            case = deepcopy(program["scenario"])
            case["events"][1]["values"] = values(amount=amount)
            trace = simulate(program, LINES, case, WITNESS)
            self.assertEqual(trace["first_difference"] and trace["first_difference"]["step"], expected)
            self.assertEqual(trace["final"]["first"]["count"], 1)
            self.assertEqual(trace["final"]["second"]["count"], 1 + int(amount >= 5000))

    def test_mutations_cannot_modify_rules_events_or_readouts(self):
        p = ledgers()
        for mutator in (
                lambda c: c.update(first=p["second"]),
                lambda c: c["events"][0].update(kind="missing"),
                lambda c: c["events"].append(c["events"][0]),
                lambda c: c["events"][0]["values"].append({"key": "override", "value": 2}),
                lambda c: c["initial"].pop(),
                lambda c: c["events"][0]["values"][0].update(value=2**53)):
            case = deepcopy(p["scenario"])
            mutator(case)
            with self.assertRaises(ValueError):
                simulate(p, LINES, case)
        # A new sample cannot rehabilitate a false original certificate.
        p["scenario"] = deepcopy(p["control"])
        with self.assertRaises(ValueError):
            simulate(p, LINES, ledgers()["scenario"])

    def test_reset_to_zero_on_an_event_is_a_real_transition(self):
        p = {"states": fields(held="₸"), "inputs": fields(capture="₸"), "observe": ["held"],
             "scenario": {"initial": values(held=9400), "events": [event("clear", capture=1700)]},
             "control": {"initial": values(held=9400), "events": [event("clear", capture=9400)]},
             "first": {"source": SOURCES["first"], "rules": [update("clear", "held", "held - capture")]},
             "second": {"source": SOURCES["second"], "rules": [update("clear", "held", "0")]}}
        self.assertEqual(execute(p, LINES)["scenario"]["first_difference"],
                         {"step": 1, "field": "held", "first": 7700, "second": 0})

    def test_an_intermediate_difference_is_reported_even_after_reconvergence(self):
        program = ledgers()
        for side in ("first", "second"):
            program[side]["rules"].extend(update("clear", key, f"{key} - {key}") for key in ("jan", "feb"))
        for name in ("scenario", "control"):
            program[name]["events"].append(event("clear"))
        result = execute(program, LINES)["scenario"]
        self.assertEqual(result["first_difference"]["step"], 1)
        self.assertEqual(result["final"]["first"], result["final"]["second"])

    def test_rejects_equal_scenario_divergent_control_and_staged_control_events(self):
        for change in ("align", "equal_control", "divergent_control", "different_events", "missing_input", "state_label"):
            with self.subTest(change=change), self.assertRaises(ValueError):
                program = ledgers()
                if change == "align":
                    program["second"]["rules"] = deepcopy(program["first"]["rules"])
                elif change == "equal_control":
                    program["control"] = deepcopy(program["scenario"])
                elif change == "divergent_control":
                    program["control"]["events"][0]["values"] = values(amount=6000, bought=1, posted=2)
                elif change == "different_events":
                    program["control"]["events"][0]["kind"] = "cancel"
                elif change == "missing_input":
                    program["control"]["events"][0]["values"].pop()
                else:
                    program["control"]["initial"][0]["key"] = "jan_control"
                execute(program, LINES)

    def test_requires_exact_source_for_each_side_and_rejects_quoted_answer_as_code(self):
        program = ledgers()
        for ref in ({"line": 38, "quote": "Буквально выдумано"}, SOURCES["second"],
                    {"line": 38, "quote": PURCHASE[:12]}):
            bad = deepcopy(program)
            bad["first"]["source"] = ref
            with self.assertRaises(ValueError):
                execute(bad, LINES, WITNESS)
        for rule in (update("settled", "jan", "4000"), update("settled", "jan", "8000 // 2", "1 == 1")):
            bad = deepcopy(program)
            bad["first"]["rules"] = [rule]
            with self.assertRaisesRegex(ValueError, "готовый ответ"):
                execute(bad, LINES)

    def test_type_unit_and_numeric_limits(self):
        for invalid in ("jan + posted", "jan * feb", "amount // posted", "jan + True", "1 if amount else 0",
                        "1 if amount > 0 else False", "True", "jan + 0.1", "jan + 9007199254740992",
                        "jan + amount * 5000000000000", "jan // 0", "None", "unknown", "max(jan, posted)"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                p = ledgers()
                p["first"]["rules"][0]["updates"][0]["expr"] = invalid
                execute(p, LINES)
        for v in (True, 2.5, 2**53, "4000"):
            with self.subTest(value=v), self.assertRaises(ValueError):
                execute(ledgers(v), LINES)

    def test_only_documented_expression_grammar_is_accepted_including_inactive_branches(self):
        for invalid in ("globals()", "amount.real", "jan[0]", "lambda: 0", "2 ** amount", "abs(*[jan])",
                        "missing if False else jan", "1 << amount", "'a' * amount", "max(jan, 1, 2)",
                        "abs(value=jan)", "jan; amount", "(jan := amount)"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                p = ledgers()
                p["first"]["rules"][0]["updates"][0]["expr"] = invalid
                execute(p, LINES)
        p = threshold_program()
        p["first"]["rules"][0]["updates"][0]["expr"] = "amount"
        with self.assertRaisesRegex(ValueError, "в этом событии"):
            execute(p, LINES)

    def test_finite_numbers_and_transforms_preserve_units(self):
        options = ("max(0, min(7000, abs(-amount)))", "amount * 2 // 2", "amount if amount >= 0 else 0",
                   "amount + amount % 2")
        for expression in options:
            with self.subTest(expression=expression):
                p = ledgers()
                for s in ("first", "second"):
                    for rule in p[s]["rules"]:
                        dest = rule["updates"][0]
                        dest["expr"] = dest["target"] + " + (" + expression + ")"
                self.assertEqual(execute(p, LINES)["scenario"]["first_difference"]["first"], 4000)

    def test_closed_schema_and_consistent_concurrent_assignments(self):
        p = ledgers()
        for target in ["unknown", "amount"]:
            corrupted = deepcopy(p)
            corrupted["first"]["rules"][0]["updates"][0]["target"] = target
            with self.assertRaises(ValueError):
                execute(corrupted, LINES)
        duplicate = deepcopy(p)
        duplicate["first"]["rules"].append(duplicate["first"]["rules"][0])
        with self.assertRaisesRegex(ValueError, "Два сработавших"):
            execute(duplicate, LINES)
        p["first"]["expected_result"] = {"jan": 4000}
        with self.assertRaises(ValueError):
            execute(p, LINES)


if __name__ == "__main__":
    unittest.main()
