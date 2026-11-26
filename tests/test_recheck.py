from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from reviewer.full import AXES
from reviewer.recheck import CHANGED, PERSISTS, RESOLVED, UNKNOWN, recheck, render_html
from reviewer.segments import segment


OLD = [
    "All purchases use the authorization month.",
    "All purchases use the settlement month.",
    "Repeat alerts are allowed after a limit increase.",
    "Only one alert per month.",
]
NEW = ["All purchases use the settlement month, regardless of authorization date.", *OLD[1:]]


def cite(line, quote):
    return {"line": line, "quote": quote}


def finding(identifier, evidence, priority="критический"):
    return dict(id=identifier, title=f"Finding {identifier}", category="нарушение", priority=priority,
                criterion="согласованность", reason="Два разных исхода.", impact="Неясное поведение.",
                owner="Владелец", question="Какой исход?", action="Уточнить правило.",
                requirements=[], candidates=[], evidence=evidence)


def decision(identifier, status, evidence=(), changes=()):
    return dict(id=identifier, status=status, reason="Перепроверено.", revised_evidence=list(evidence),
                diff_hunks=list(changes))


def confirmed():
    return {"checks": [
        decision("D-02", PERSISTS, [cite(3, NEW[2]), cite(4, NEW[3])]),
        decision("D-01", RESOLVED, [cite(1, NEW[0]), cite(2, NEW[1])], ["C-001"]),
    ]}


class RecheckTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name)
        self.previous = directory / "previous.json"
        self.revised = directory / "revised.md"
        self.report = {
            "schema_version": 2,
            "run": {"input_lines": len(OLD), "input_name": "previous.md", "agent_version": "test",
                    "input_sha256": sha256(self.document(OLD)).hexdigest()},
            "source": [{"line": i, "text": text} for i, text in enumerate(OLD, 1)],
            "conflicts": [],
            "registry": [],
            "whole": {"findings": [
                finding("D-01", [cite(1, OLD[0]), cite(2, OLD[1])]),
                finding("D-02", [cite(3, OLD[2]), cite(4, OLD[3])]),
                finding("D-03", [cite(3, OLD[2])], priority="существенный"),
            ]},
        }
        gate = {"status": "можно частично", "rationale": "Есть открытое решение.",
                "evidence": [cite(1, OLD[0])]}
        self.report["whole"].update(
            dimensions=[{"criterion": axis, "assessment": "В рамках проверки.",
                         "evidence": [cite(1, OLD[0])]} for axis in AXES],
            reconciled=[], development=gate, testing=gate,
        )
        self.report["segments"] = [dict(block, note="Часть условия.", requirements=[]) for block in segment(OLD)]
        self.report["whole"]["findings"][1]["candidates"] = [1]
        self.report["conflicts"] = [
            {"title": "Частота уведомлений", "scenario": "Лимит повышен.",
             "first": {"source": cite(3, OLD[2]), "outcome": "Повторный сигнал."},
             "second": {"source": cite(4, OLD[3]), "outcome": "Без сигнала."},
             "owner": "Владелец", "question": "Будет ли сигнал?", "agreement_control": "Первый сигнал."}
        ]
        self.write_previous()
        self.revised.write_bytes(self.document(NEW))

    @staticmethod
    def document(lines):
        return ("\n".join(lines) + "\n").encode()

    def write_previous(self):
        self.previous.write_text(json.dumps(self.report), encoding="utf-8")

    @patch("reviewer.full.codex_provider.complete")
    def test_mixed_result_matches_diff_and_records_inputs(self, complete):
        complete.return_value = confirmed(), {"input_tokens": 20, "output_tokens": 10}
        report = recheck(self.previous, self.revised, "mock")
        self.assertEqual([check["status"] for check in report["checks"]], [RESOLVED, PERSISTS])
        self.assertEqual(report["selection"], "critical")
        self.assertEqual(report["diff"][0]["before"], [{"line": 1, "text": OLD[0]}])
        self.assertEqual(report["diff"][0]["after"], [{"line": 1, "text": NEW[0]}])
        self.assertEqual(report["run"]["previous_report_sha256"], sha256(self.previous.read_bytes()).hexdigest())
        self.assertEqual(report["run"]["input_sha256"], sha256(self.revised.read_bytes()).hexdigest())
        self.assertEqual(report["run"]["usage"]["input_tokens"], 20)
        prompt = complete.call_args.args[0]
        self.assertIn(OLD[0], prompt)
        self.assertIn(NEW[0], prompt)
        self.assertNotIn("D-03", prompt)
        html = render_html(report)
        self.assertIn('href="#C-001"', html)
        self.assertIn('id="C-001"', html)
        self.assertIn('href="#line-1"', html)
        self.assertIn('id="line-1"', html)

    @patch("reviewer.full.codex_provider.complete")
    def test_unchanged_document_is_deterministic_and_respects_explicit_selection(self, complete):
        self.revised.write_bytes(self.document(OLD))
        report = recheck(self.previous, self.revised, "mock", ["D-03"])
        complete.assert_not_called()
        self.assertEqual(report["selection"], "explicit_ids")
        self.assertTrue(report["run"]["source_unchanged"])
        self.assertEqual(report["run"]["usage"], {})
        self.assertEqual(report["diff"], [])
        self.assertEqual([(row["id"], row["status"]) for row in report["checks"]], [("D-03", PERSISTS)])

    @patch("reviewer.full.codex_provider.complete")
    def test_retries_duplicate_or_missing_ids_and_sums_usage(self, complete):
        valid = confirmed()
        repeated = valid["checks"][:1] * 2
        complete.side_effect = [({"checks": repeated}, {"input_tokens": 10}), (valid, {"input_tokens": 12})]
        run = recheck(self.previous, self.revised, "mock")["run"]
        self.assertEqual([event["status"] for event in run["attempts"]], ["rejected", "accepted"])
        self.assertEqual(run["usage"], {"input_tokens": 22})

    @patch("reviewer.full.codex_provider.complete")
    def test_rejects_old_quote_and_unrelated_hunk_reference(self, complete):
        for evidence in ([cite(1, OLD[0])], [cite(2, NEW[1])]):
            with self.subTest(evidence=evidence):
                output = {"checks": [decision("D-01", CHANGED, evidence, ["C-001"])]}
                complete.return_value = output, {}
                with self.assertRaises(ValueError):
                    recheck(self.previous, self.revised, "mock", ["D-01"])

    @patch("reviewer.full.codex_provider.complete")
    def test_deletion_alone_is_not_resolution(self, complete):
        self.revised.write_bytes(self.document(OLD[1:]))
        output = {"checks": [decision("D-01", RESOLVED, [cite(1, OLD[1])], ["C-001"])]}
        complete.return_value = output, {}
        with self.assertRaises(ValueError):
            recheck(self.previous, self.revised, "mock", ["D-01"])
        output["checks"][0]["status"] = UNKNOWN
        report = recheck(self.previous, self.revised, "mock", ["D-01"])
        self.assertEqual(report["checks"][0]["status"], UNKNOWN)
        self.assertEqual(report["diff"][0]["operation"], "delete")

    @patch("reviewer.full.codex_provider.complete")
    def test_rejects_tampered_previous_source_and_unknown_selection(self, complete):
        with self.assertRaisesRegex(ValueError, "Неизвестные замечания"):
            recheck(self.previous, self.revised, "mock", ["D-99"])
        self.report["whole"]["findings"][0]["evidence"][0]["quote"] = "invented"
        self.write_previous()
        with self.assertRaisesRegex(ValueError, "Цитата не подтверждена"):
            recheck(self.previous, self.revised, "mock")
        complete.assert_not_called()

    @patch("reviewer.full.codex_provider.complete")
    def test_persistent_conflict_requires_two_distinct_quotes(self, complete):
        single_side = cite(3, OLD[2])
        complete.return_value = {"checks": [decision("D-02", PERSISTS, [single_side, single_side])]}, {}
        with self.assertRaisesRegex(ValueError, "две разные цитаты"):
            recheck(self.previous, self.revised, "mock", ["D-02"])

    @patch("reviewer.full.codex_provider.complete")
    def test_whitespace_does_not_justify_fixed_status(self, complete):
        lines = [OLD[0].replace("purchases use", "purchases    use"), *OLD[1:]]
        self.revised.write_bytes(self.document(lines))
        complete.return_value = {"checks": [decision("D-01", RESOLVED, [cite(1, lines[0])])]}, {}
        with self.assertRaisesRegex(ValueError, "содержательно изменённую"):
            recheck(self.previous, self.revised, "mock", ["D-01"])
