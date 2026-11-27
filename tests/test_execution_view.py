from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from xml.etree import ElementTree

from reviewer.contract import source_lines
from reviewer.executable import simulate
from reviewer.execution_view import explain_simulation, render_execution, render_explanation
from reviewer.full_render import decision_brief, execution_markdown
from reviewer.serve import Workbench, WorkbenchError
from reviewer.witness_vm import execute


ROOT = Path(__file__).resolve().parents[1]


class ExecutionViewTests(TestCase):
    def test_summary_ranks_non_executable_findings_and_keeps_actions_and_sources(self):
        fallback = {"id": "D-09", "title": "Не описана отмена", "priority": "существенный",
                    "question": "Кто отменяет?", "action": "Назвать владельца <отмены>.",
                    "owner": "Операционный менеджер", "evidence": [{"line": 17}]}
        findings = [dict(fallback, id="D-08", priority="редакционный"), fallback]
        section = ElementTree.fromstring(decision_brief({"whole": {"findings": findings}}))
        self.assertEqual([c.find("small/a").text for c in section.findall("article")], ["D-09", "D-08"])
        card = section.find("article")
        self.assertIn(fallback["action"], "".join(card.itertext()))
        self.assertEqual(card.find("p[@class='brief-refs']/a").get("href"), "#source-line-17")
        self.assertNotIn("Расчёт и контроль", "".join(card.itertext()))
        self.assertIn("замечаний для согласования нет", decision_brief({"whole": {"findings": []}}))

    def test_first_divergent_step_shows_every_changed_observable_in_card_brief_and_handoff(self):
        report = json.loads((ROOT / "eval/executable/runs/hotel.json").read_bytes())
        item = report["executables"]["items"][0]
        program = item["program"]
        program["observe"] = ["balance", "held", "available"]
        item["result"] = execute(program, source_lines(report))

        expected = [("Сумма, заблокированная по авторизации", "11 200 ₸", "0 ₸"),
                    ("Доступный остаток в приложении", "43 300 ₸", "54 500 ₸")]
        panel = ElementTree.fromstring(render_execution(item))
        self.assertEqual([h.text for h in panel.findall("h4")], [row[0] for row in expected])
        comparisons = panel.findall('dl[@class="execution-comparison"]')
        self.assertEqual([[n.text for n in dl.findall("div/dd")] for dl in comparisons],
                         [[first, second] for _, first, second in expected])
        brief = ElementTree.fromstring(decision_brief(report)).find("article")
        self.assertEqual([h.text for h in brief.findall("h3")], [row[0] for row in expected])
        self.assertEqual([s.text for s in brief.findall("strong")],
                         [f"{first} или {second}" for _, first, second in expected])
        digest = execution_markdown(item).split("Перед первым событием", 1)[0]
        for label, first, second in expected:
            self.assertIn(f"{label}. Правило А даёт {first}, правило Б даёт {second}.", digest)
        self.assertNotIn("Учётный остаток", digest)

    def test_replays_and_handoffs_preserve_exact_non_numeric_api_values(self):
        states = [{"key": "status", "label": "Ответ на заявку", "unit": "enum:status"}]
        first, second = "AWAITING_SIGNATURE", "MANUAL_REVIEW"
        start = [{"key": "status", "value": "READY"}]
        program = {"states": states, "inputs": [], "observe": ["status"], "scenario": {"initial": start},
                   "control": {"initial": start}, "first": {"source": {"line": 2, "quote": "Первый ответ"}},
                   "second": {"source": {"line": 3, "quote": "Второй ответ"}}}
        def replay(a, b):
            ends = {"first": {"status": a}, "second": {"status": b}}
            step = {"step": 1, "label": "Запрос API", "inputs": {}, **ends}
            return {"steps": [step], "first_difference": None if a == b else
                    {"step": 1, "field": "status", "first": a, "second": b}, "final": ends}

        item = {"finding_id": "D-04", "status": "executed", "program": program,
                "result": {"scenario": replay(first, second), "control": replay("REJECTED", "REJECTED")}}
        panel = ElementTree.fromstring(render_execution(item))
        self.assertEqual(["".join(e.itertext()) for e in panel.findall('dl[@data-values="enum"]/div/dd')],
                         [first, second])
        text = "".join(panel.itertext())
        self.assertIn("REJECTED", text)
        self.assertNotIn("enum:status", text)
        md = execution_markdown(item)
        for literal in (first, second, "READY", "REJECTED"):
            self.assertIn(f"`{literal}`", md)
        self.assertNotIn("enum:status", md)

    def test_amount_outside_the_table_case_shows_the_actually_skipped_guard(self):
        report = json.loads((ROOT / "eval/executable/runs/official.json").read_bytes())
        item = report["executables"]["items"][0]
        edited = deepcopy(item["program"]["scenario"])
        amount = next(row for row in edited["events"][0]["values"] if row["value"] == 4000)
        amount["value"] = 4100
        trace = simulate(report, item["finding_id"], edited)
        html = explain_simulation(report, item["finding_id"], edited, trace)
        self.assertIsNone(trace["first_difference"])
        self.assertIn("Как рассчитан шаг 1", html)
        self.assertIn('href="#source-line-64"', html)
        self.assertIn("Осталось 0 ₸", html)
        self.assertIn("4\u202f100 ₸", html)
        self.assertNotIn("Почему разошлись", html)

    def test_explanation_displays_actual_guard_reads_and_preserved_value(self):
        row = {"when": "amount < 10", "matched": False, "reads": {"amount": 12},
               "updates": [{"target": "total", "before": 1, "after": 1, "applied": False,
                            "expr": "total + amount", "reads": {}}]}
        explanation = {"step": 2, "first": [row], "second": []}
        defs = {"amount": {"label": "Начисление", "unit": "₸"}, "total": {"label": "Итог", "unit": "₸"}}
        sources = {key: {"source": {"line": n}} for key, n in (("first", 2), ("second", 3))}
        html = render_explanation(explanation, sources, defs)
        self.assertIn("Почему разошлись на шаге 2", html)
        self.assertIn("amount &lt; 10", html)
        self.assertIn("12 ₸", html)
        self.assertIn("Осталось 1 ₸", html)
        self.assertNotIn("total + amount", html)
        self.assertIn("сохраняет прежнее значение", html)

    def test_trace_identifies_first_difference_even_if_the_final_values_converge(self):
        initial = [{"key": "balance", "value": 0}]
        def step(number, first, second):
            return {"step": number, "label": '<Платёж>', "inputs": {"amount": 12000},
                    "first": {"balance": first}, "second": {"balance": second}}

        program = {"states": [{"key": "balance", "label": "Баланс", "unit": "₸"}],
                   "inputs": [{"key": "amount", "label": "Сумма", "unit": "₸"}],
                   "observe": ["balance"], "scenario": {"initial": initial}, "control": {"initial": initial}}
        for side, line in (("first", 2), ("second", 8)):
            program[side] = {"source": {"line": line, "quote": "Платёж < 15 000 ₸"}}
        result = {"scenario": {"steps": [step(1, 0, 12000), step(2, 12000, 12000)],
                               "first_difference": {"step": 1, "field": "balance", "first": 0, "second": 12000},
                               "final": {"first": {"balance": 12000}, "second": {"balance": 12000}}},
                  "control": {"steps": [step(1, 12000, 12000)], "first_difference": None}}
        html = render_execution({"status": "executed", "finding_id": "D-05", "program": program, "result": result})
        panel = ElementTree.fromstring(html)
        self.assertEqual(panel.attrib["id"], "execution-D-05")
        quantities = panel.findall('dl[@class="execution-comparison"]/div/dd')
        self.assertEqual([q.text for q in quantities], ["0 ₸", "12\u202f000 ₸"])
        self.assertIn("Первое расхождение · шаг 1", "".join(panel.itertext()))
        sources = panel.findall('details[@class="execution-sources"]/blockquote/span/a')
        self.assertEqual([a.attrib["href"] for a in sources], ["#source-line-2", "#source-line-8"])
        direct_final = panel.findall("table/tbody/tr/td")
        self.assertEqual([td.text for td in direct_final], ["12\u202f000 ₸"] * 2)
        self.assertNotIn("<Платёж>", html)
        control = panel.find('details[@class="execution-control"]')
        self.assertEqual(control.find("summary").text, "Контрольный пример · исходы совпали")

    def test_workbench_binds_fragments_to_validated_bytes_and_rejects_forged_results(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            demo = root / "runs" / "bcc-submission"
            demo.mkdir(parents=True)
            (demo / "review.html").write_text("Saved report")
            app = Workbench(root / "storage", root=root)
            report = json.loads((ROOT / "runs/bcc-submission/review.json").read_bytes())
            def save(value):
                raw = json.dumps(value, ensure_ascii=False).encode()
                (demo / "review.json").write_bytes(raw)
                return sha256(raw).hexdigest()
            targets = [f for f in report["whole"]["findings"] if f["priority"] == "критический"
                       and f["category"] == "нарушение" and len(f.get("triage", {}).get("witness", [])) == 1]
            items = [{"finding_id": f["id"], "status": "unsupported", "reason": "Нет точной суммы <ликвидации>."}
                     for f in targets]
            self.assertTrue(items)
            report["executables"] = {"version": "witness-v1", "items": items}
            digest = save(report)
            panels = app.execution_panels("demo", digest)
            self.assertEqual(panels["sha256"], digest)
            card = panels["items"][items[0]["finding_id"]]
            self.assertIn("Расчёт не подтверждён", card)
            self.assertIn("&lt;ликвидации&gt;", card)
            self.assertNotIn("execution-side", card)
            forged = deepcopy(report)
            forged["executables"]["items"][0].update(status="executed")
            new_digest = save(forged)
            for sha, code in ((digest, 409), (new_digest, 422)):
                with self.assertRaises(WorkbenchError) as failure:
                    app.execution_panels("demo", sha)
                self.assertEqual(failure.exception.code, code)
