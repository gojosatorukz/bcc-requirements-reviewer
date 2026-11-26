from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from reviewer.__main__ import review_document
from reviewer.contract import HTTP_STATUS_POLICY, explicit_http_statuses, source_http_statuses, validate_review
from reviewer.render import render_report

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = ROOT / "eval/public-payments/runs/mutated.json"


def conflict(scenario="Получен HTTP-ответ об ошибке, ключ p-17."):
    return {
        "scenario": scenario,
        "first": {"source": {"line": 1, "quote": "Остановиться."}, "outcome": "Остановиться."},
        "second": {"source": {"line": 2, "quote": "Повторить."}, "outcome": "Повторить."},
        "title": "Повтор запроса", "owner": "Владелец", "question": "Нужен ли повтор?",
        "agreement_control": "Контроль не задан.",
    }


class HttpStatusGroundingTests(unittest.TestCase):
    def test_http_notation_recognizes_lists_versions_labels_and_status_phrases(self):
        examples = {
            "HTTP 409 или 422": {"409", "422"},
            "HTTP/1.1 200": {"200"},
            "HTTP-статус: 409 либо `422`": {"409", "422"},
            "status code **409**, **422** or 503": {"409", "422", "503"},
            "код ответа 404, код платёжной ошибки 704": {"404"},
            "получен 503 Service Unavailable": {"503"},
            '"httpStatus": 200': {"200"},
            "HTTP 200 и 503 ₸": {"200"},
            "05.11.2026: сумма 503 тенге, ключ p-17, бизнес-код 704": set(),
        }
        for text, codes in examples.items():
            with self.subTest(text=text):
                self.assertEqual(explicit_http_statuses(text), codes)

    def test_source_table_values_require_an_http_status_column(self):
        lines = {
            1: "| HTTP status code | Бизнес-код |",
            2: "| --- | --- |",
            3: "| `409` | 404 |",
            7: "| 503 | 504 |",
            8: "Стоимость 502 тенге.",
        }
        self.assertEqual(source_http_statuses(lines, HTTP_STATUS_POLICY), {"409"})

    def test_official_source_marks_http_statuses_in_response_sections(self):
        official = json.loads((ROOT / "runs/bcc-final/review.json").read_text(encoding="utf-8"))
        lines = {row["line"]: row["text"] for row in official["source"]}
        self.assertEqual(source_http_statuses(lines, HTTP_STATUS_POLICY),
                         {"200", "201", "400", "401", "403", "404", "500"})

    def test_requires_exact_status_and_accepts_abstract_class(self):
        lines = ["Остановиться.", "Повторить.", "Возвращается HTTP 4xx."]
        item = conflict("Получен HTTP 4xx, дата 31.01.2026, сумма 503 ₸, ключ p-17.")
        result = {"conflicts": [item]}
        validate_review(result, lines, http_status_policy=HTTP_STATUS_POLICY)
        for field in ("scenario", "first", "second"):
            with self.subTest(field=field):
                invalid = deepcopy(result)
                candidate = invalid["conflicts"][0]
                if field == "scenario":
                    candidate[field] = "Получен HTTP 409."
                else:
                    candidate[field]["outcome"] = "Возвращён HTTP 409, код ошибки 704."
                with self.assertRaisesRegex(ValueError, "HTTP-статусы 409 не указаны"):
                    validate_review(invalid, lines, http_status_policy=HTTP_STATUS_POLICY)
                validate_review(invalid, lines + ["API: HTTP 409 или 422."],
                                http_status_policy=HTTP_STATUS_POLICY)

    def test_conflicts_only_retries_an_ungrounded_status(self):
        lines = ["Остановиться.", "Повторить.", "При получении HTTP-ответа об ошибке."]
        invalid = {"conflicts": [conflict("Получен HTTP 503.")]}
        repaired = {"conflicts": [conflict()]}
        with patch("reviewer.codex_provider.complete", side_effect=[(invalid, {}), (repaired, {})]) as complete:
            response, attempts = review_document("инструкция", "test-model", lines)
        self.assertEqual(response, repaired)
        self.assertEqual([entry["status"] for entry in attempts], ["rejected", "accepted"])
        self.assertIn("HTTP-статусы 503", complete.call_args.args[0])

    def test_historical_report_replays_but_new_policy_covers_candidate_and_triage(self):
        old = json.loads(HISTORICAL.read_text(encoding="utf-8"))
        self.assertNotIn("http_status_policy", old)
        self.assertIn("HTTP 503", render_report(old))
        new = deepcopy(old)
        new["http_status_policy"] = HTTP_STATUS_POLICY
        with self.assertRaisesRegex(ValueError, r"Конфликт 1\.scenario: HTTP-статусы 503"):
            render_report(new)
        new["conflicts"][0]["scenario"] = conflict()["scenario"]
        witness = new["whole"]["findings"][0]["triage"]["witness"][0]
        witness["scenario"] = conflict()["scenario"]
        previous = witness["second"]["outcome"]
        witness["second"]["outcome"] = "Позже отправляется второй POST после ответа HTTP 503."
        with self.assertRaisesRegex(ValueError, r"свидетельство\.second\.outcome: HTTP-статусы 503"):
            render_report(new)
        witness["second"]["outcome"] = previous
        self.assertNotIn("HTTP 503", render_report(new))


if __name__ == "__main__":
    unittest.main()
