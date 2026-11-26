from copy import deepcopy
from unittest import TestCase

from reviewer.contract import validate_shape
from reviewer.witness_vm import execute, explain_step, simulate


LINES = {1: "Статус доставки: WAIT, HELD, READY, CANCELLED.",
         2: "Ответ перевозчика: WAIT, отказ клиента, доставлено.",
         3: "При ответе WAIT сохранить WAIT.",
         4: "При ответе WAIT вернуть HELD.",
         5: "Если доставлено, вернуть READY. При отказе клиента вернуть CANCELLED."}


def ref(line):
    return {"line": line, "quote": LINES[line]}


def domain(name, line, values):
    return {"key": name, "options": [{"value": value, "source": ref(line)} for value in values]}


def sample(value):
    return {"initial": [{"key": "status", "value": "WAIT"}], "events": [
        {"kind": "dispatch", "label": "Получить ответ", "values": [{"key": "response", "value": value}]}]}


def program():
    def side(line, disputed):
        expression = f"'{disputed}' if response == 'WAIT' else ('CANCELLED' if response == 'отказ клиента' else 'READY')"
        return {"source": ref(line), "rules": [{"event": "dispatch", "when": "True",
                "updates": [{"target": "status", "expr": expression}]}]}

    return {"domains": [domain("status", 1, ("WAIT", "HELD", "READY", "CANCELLED")),
                        domain("response", 2, ("WAIT", "отказ клиента", "доставлено"))],
            "states": [{"key": "status", "label": "Статус", "unit": "enum:status"}],
            "inputs": [{"key": "response", "label": "Ответ перевозчика", "unit": "enum:response"}],
            "observe": ["status"], "first": side(3, "WAIT"), "second": side(4, "HELD"),
            "scenario": sample("WAIT"), "control": sample("доставлено")}


class NamedDomainTests(TestCase):
    def test_returns_exact_types_and_supports_unchosen_case_and_explanation(self):
        p = program()
        self.assertEqual(execute(p, LINES)["scenario"]["first_difference"],
                         {"step": 1, "field": "status", "first": "WAIT", "second": "HELD"})
        for value, expected in (("отказ клиента", "CANCELLED"), ("доставлено", "READY")):
            result = simulate(p, LINES, sample(value))
            self.assertIsNone(result["first_difference"])
            self.assertEqual(result["final"], {side: {"status": expected} for side in ("first", "second")})
        detail = explain_step(p, LINES, case=sample("отказ клиента"), step=1, field="status")
        change = detail["first"][0]["updates"][0]
        self.assertEqual((change["before"], change["after"], change["reads"]),
                         ("WAIT", "CANCELLED", {"response": "отказ клиента"}))

    def test_asserts_nominal_domain_on_all_paths(self):
        for expr in ("response", "status + 'HELD'", "'offline' if response == 'WAIT' else 'READY'",
                     "'WAIT' if response > 'WAIT' else 'READY'", "min(status, status)", "abs(status)",
                     "1 if response == 'WAIT' else 'READY'", "'READY' if response == status else 'HELD'",
                     "'READY' if response == 'HELD' else 'WAIT'"):
            with self.subTest(expr=expr), self.assertRaises(ValueError):
                p = program()
                p["first"]["rules"][0]["updates"][0]["expr"] = expr
                execute(p, LINES)
        for wrong in (0, True, "Ready", "отказ"):
            with self.subTest(value=wrong), self.assertRaises(ValueError):
                simulate(program(), LINES, sample(wrong))

    def test_option_must_appear_as_a_whole_token_in_the_claimed_source(self):
        for claim in ("ready", "READY", "REA", "RE", "PAID"):
            with self.subTest(claim=claim), self.assertRaises(ValueError):
                p = program()
                option = p["domains"][0]["options"][2]
                option.update(value=claim, source={"line": 7, "quote": claim})
                execute(p, {**LINES, 7: "NOT_READY / readyish / PRE-PAID — выбор получателя"})
        p = program()
        p["domains"][0]["options"][2]["source"] = {"line": 7, "quote": "READY"}
        self.assertIsNotNone(execute(p, {**LINES, 7: "NOT_READY, затем READY."})["scenario"]["first_difference"])

    def test_rejects_undeclared_duplicate_unused_and_unconditional_outputs(self):
        originals = program()
        for mutate in (lambda p: p["inputs"][0].update(unit="enum:missing"),
                       lambda p: p["domains"].append(deepcopy(p["domains"][0])),
                       lambda p: p["domains"][0]["options"].append(deepcopy(p["domains"][0]["options"][0])),
                       lambda p: p["domains"].append(domain("unused", 1, ["WAIT"])),
                       lambda p: p["first"]["rules"][0]["updates"][0].update(expr="'READY'")):
            p = deepcopy(originals)
            mutate(p)
            with self.assertRaises(ValueError):
                execute(p, LINES)

    def test_numeric_condition_can_select_a_grounded_named_result(self):
        p = program()
        p["domains"].pop()
        p["inputs"] = [{"key": "stock", "label": "Доступный остаток", "unit": "шт."}]
        for case, stock in (("scenario", 1), ("control", 0)):
            p[case]["events"][0]["values"] = [{"key": "stock", "value": stock}]
        for side, cutoff in (("first", 1), ("second", 0)):
            p[side]["rules"][0]["updates"][0]["expr"] = f"'HELD' if stock > {cutoff} else 'WAIT'"
        trace = execute(p, LINES)
        self.assertEqual(trace["scenario"]["final"], {"first": {"status": "WAIT"}, "second": {"status": "HELD"}})

    def test_json_type_union_distinguishes_boolean_and_numeric_fields_stay_numeric(self):
        scalar = {"type": ["integer", "string"]}
        for value in (1, "READY"):
            validate_shape(value, scalar, "test")
        for value in (None, True, 0.5, [], {}, ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_shape(value, scalar, "test")
        p = program()
        p["states"][0]["unit"] = "₸"
        with self.assertRaises(ValueError):
            execute(p, LINES)
