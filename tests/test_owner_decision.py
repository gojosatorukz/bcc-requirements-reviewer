from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from reviewer import owner_decision
from reviewer.recheck import recheck, render_html


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runs/bcc-submission/review.json"
REVISED = ROOT / "eval/owner-decision/february.md"
BASE_SHA = sha256(BASE.read_bytes()).hexdigest()


def source(n):
    return {"line": n, "quote": REVISED.read_text().splitlines()[n - 1]}


def result():
    return {"checks": [{"id": "D-01", "status": "устранено", "reason": "Месяц проведения.",
                        "revised_evidence": [source(38), source(64)], "diff_hunks": ["C-001"]}],
            "observations": [{"id": "D-01", "observed_side": "second", "reason": "Платёж попадает в февраль.",
                              "evidence": [source(38), source(64)]}]}


class OwnerDecisionTests(TestCase):
    def test_same_prompt_compares_opposite_choices_without_disclosing_them_to_model(self):
        with patch("reviewer.full.codex_provider.complete", side_effect=lambda *args: (result(), {})) as model:
            reports = [recheck(BASE, REVISED, "test", ["D-01"], decisions=["D-01=" + side],
                               baseline_sha256=BASE_SHA) for side in ("first", "second")]
        self.assertEqual(model.call_args_list[0], model.call_args_list[1])
        first, second = [report["checks"][0] for report in reports]
        self.assertEqual([first["status"], second["status"]], ["устранено"] * 2)
        self.assertEqual(first["owner_decision"]["status"], owner_decision.MISMATCH)
        self.assertEqual(second["owner_decision"]["status"], owner_decision.MATCH)
        self.assertIn(owner_decision.MISMATCH, render_html(reports[0]))
        forged = deepcopy(reports[0])
        forged["checks"][0]["owner_decision"]["status"] = owner_decision.MATCH
        with self.assertRaises(ValueError):
            render_html(forged)

    def test_unchanged_real_source_never_confirms_choosing_a_side(self):
        import tempfile
        base = json.loads(BASE.read_bytes())
        with tempfile.TemporaryDirectory() as temporary, patch("reviewer.full.codex_provider.complete") as model:
            path = Path(temporary) / "spec.md"
            lines = [""] * base["run"]["input_lines"]
            for row in base["source"]:
                lines[row["line"] - 1] = row["text"]
            path.write_text("\n".join(lines) + "\n")
            report = recheck(BASE, path, "test", decisions=["D-01=first"], baseline_sha256=BASE_SHA)
        model.assert_not_called()
        d1, d2 = report["checks"]
        self.assertEqual(d1["owner_decision"]["status"], owner_decision.UNCONFIRMED)
        self.assertEqual(d1["status"], "сохранилось")
        self.assertNotIn("owner_decision", d2)
        self.assertIn(owner_decision.UNCONFIRMED, render_html(report))

    def test_invalid_or_misbound_decisions_do_not_call_model(self):
        with patch("reviewer.full.codex_provider.complete") as model:
            for specs, digest in ((["D-01=first"], None), (["D-01=first"], "0" * 64),
                                  (["D-01=first", "D-01=second"], BASE_SHA), (["D-99=first"], BASE_SHA),
                                  (["D-01=other"], BASE_SHA), ([], BASE_SHA)):
                with self.subTest(specs=specs, digest=digest), self.assertRaises(ValueError):
                    recheck(BASE, REVISED, "test", decisions=specs, baseline_sha256=digest)
        model.assert_not_called()

    def test_observation_needs_evidence_at_the_relevant_change(self):
        data = result()
        check, = data["checks"]
        obs, = data["observations"]
        diff = [{"id": "C-001", "after": [source(38)]}]
        lines = {i: source(i)["quote"] for i in (38, 64)}
        for broken in ([], [obs, obs], [dict(obs, evidence=[source(64)])],
                       [dict(obs, evidence=[{"line": 38, "quote": "выдумано"}])]):
            with self.assertRaises(ValueError):
                owner_decision.verify_observations(broken, ["D-01"], [check], lines, diff)
        with self.assertRaises(ValueError):
            owner_decision.verify_observations([obs], ["D-01"], [dict(check, status="сохранилось")], lines, diff)
