import io
import json
import os
from pathlib import Path
from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase
from unittest.mock import patch

from reviewer import codex_provider, gemini_provider, lifecycle, openai_provider
from reviewer.__main__ import main
from reviewer.contract import record
from reviewer.full import AXES
from reviewer.full_render import validate_report
from reviewer.render import render_report


RULE = "Термометр показывает температуру в кельвинах."
MODEL = "gpt-6.2-sol"
SNAPSHOT = MODEL + "-2026-11-01"
TOKEN = "sk-unit-test-only"
RESERVE = {"REVIEW_PROVIDER": "gemini", "GEMINI_API_KEY": "google-test",
           "REVIEW_FALLBACK_PROVIDER": "openai", "REVIEW_FALLBACK_MODEL": MODEL, "OPENAI_API_KEY": TOKEN}


def envelope(value):
    return {"status": "completed", "model": SNAPSHOT, "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": json.dumps(value, ensure_ascii=False)}]}],
        "usage": {"input_tokens": 35, "output_tokens": 16, "input_tokens_details": {"cached_tokens": 8},
                  "output_tokens_details": {"reasoning_tokens": 4}}}


def one_rule_output(schema):
    gate = {"status": "можно начинать", "rationale": "Единица задана.", "evidence": [1]}
    entry = {"role": "требование", "statement": RULE, "evidence": 1, "status": "проверяемо",
             "assessment": "Единица определена.", "quality_checks": [
                 {"criterion": "проверяемость", "conclusion": "Проверить единицу показания.", "evidence": 1}]}
    return {"rows": {"rows": [{"start": 1, "note": "Шкала.", "items": [entry]}]},
            "checks": {"checks": [], "skipped": [{"axis": axis, "reason": "Один шаг."} for axis in lifecycle.AXES]},
            "conflicts": {"conflicts": []},
            "dimensions": {"dimensions": [{"criterion": axis, "assessment": "Единица задана.", "evidence": [1]}
                                          for axis in AXES], "findings": [], "reconciled": [], "development": gate,
                           "testing": gate}}[next(iter(schema["properties"]))]


class OpenAIProviderTests(TestCase):
    def setUp(self):
        self.events = []
        self.google_status = 429
        self.reserve_replies = []
        case = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                case.events.append((self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                if self.path == "/v1/responses":
                    if case.reserve_replies:
                        status, payload = case.reserve_replies.pop(0)
                    else:
                        status, payload = 200, envelope(one_rule_output(body["text"]["format"]["schema"]))
                else:
                    status, payload = case.google_status, {"error": {"code": case.google_status}}
                self.send_response(status)
                self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(server.server_close)
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.03}, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        host = f"http://127.0.0.1:{server.server_port}"
        for adapter, endpoint in [(openai_provider, host + "/v1/responses"), (gemini_provider, host + "/models/")]:
            patched = patch.object(adapter, "ENDPOINT", endpoint)
            patched.start()
            self.addCleanup(patched.stop)
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name) / "thermometer.md"
        self.source.write_text(RULE, encoding="utf-8")
        self.out = self.source.with_suffix(".json")

    def run_cli(self, flags=(), **environment):
        with patch.dict(os.environ, {**RESERVE, **environment}, clear=True), patch("sys.argv", [
                "reviewer", str(self.source), str(self.out), "--workers", "1", *flags]):
            main()
        return json.loads(self.out.read_bytes())

    def test_full_rerun_over_local_http_with_structured_output_and_actual_model_receipt(self):
        result = self.run_cli()
        validate_report(result)
        run = result["run"]
        self.assertEqual((run["provider"], run["model"], run["response_models"]),
                         ("openai-responses", MODEL, [SNAPSHOT]))
        self.assertEqual(run["fallback_from"], {"provider": "gemini-api", "model": "gemini-3.5-flash-lite",
                                                "reason": "HTTP 429"})
        self.assertEqual(result["registry"][0]["statement"], RULE)
        self.assertEqual(self.events[0][1]["x-goog-api-key"], "google-test")
        self.assertNotIn("authorization", self.events[0][1])
        responses = self.events[1:]
        self.assertEqual(run["fresh_calls"], len(responses))
        self.assertEqual(run["current_usage"]["output_tokens"], len(responses) * 16)
        self.assertEqual(run["current_usage"]["reasoning_output_tokens"], len(responses) * 4)
        for path, headers, body in responses:
            self.assertEqual(path, "/v1/responses")
            self.assertEqual(headers["authorization"], "Bearer " + TOKEN)
            self.assertNotIn("x-goog-api-key", headers)
            self.assertNotIn(TOKEN, json.dumps(body))
            self.assertFalse(body["store"])
            self.assertEqual(body["tools"], [])
            self.assertTrue(body["text"]["format"]["strict"])
            self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertIn(SNAPSHOT, render_report(result))
        self.assertNotIn(TOKEN, self.out.read_text())

    def test_openai_is_opt_in_and_nonquota_primary_errors_do_not_trigger_it(self):
        for env in ({"REVIEW_FALLBACK_PROVIDER": ""}, {"OPENAI_API_KEY": ""}, {"REVIEW_FALLBACK_MODEL": ""}):
            with self.subTest(env=env), patch.dict(os.environ, {**RESERVE, **env}, clear=True):
                self.assertIsNone(codex_provider.fallback_model("gemini-test"))
        self.google_status = 403
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            self.run_cli(flags=["--partial-on-quota"])
        self.assertFalse(self.out.exists())
        self.assertFalse(any(event[0] == "/v1/responses" for event in self.events))

    def test_two_quota_failures_produce_labelled_local_report_without_retrying_spending_limit(self):
        self.reserve_replies.append((429, {"error": {"code": "insufficient_quota", "message": TOKEN}}))
        result = self.run_cli(flags=["--partial-on-quota"])
        self.assertEqual([event[0] for event in self.events].count("/v1/responses"), 1)
        self.assertEqual(result["report_type"], "partial")
        self.assertEqual(result["attempted_openai_reserve"], {"model": MODEL, "reason": "insufficient_quota"})
        self.assertNotIn("run", result)
        self.assertIn("локальными правилами", render_report(result))

    def test_direct_provider_retries_bounded_rate_limit_and_rejects_incomplete_or_invalid_replies(self):
        schema = record(answer={"type": "string"})
        with patch.dict(os.environ, {"REVIEW_PROVIDER": "openai", "OPENAI_API_KEY": TOKEN}, clear=True):
            self.reserve_replies.extend([(429, {"error": {"code": "rate_limit_exceeded"}}), (200, envelope({"answer": "ok"}))])
            self.assertEqual(codex_provider.complete("private", MODEL, schema)[0], {"answer": "ok"})
            self.assertEqual(len(self.events), 2)
            wrong = envelope({"answer": "bad"})
            wrong["output"][1]["content"] = [{"type": "refusal", "refusal": TOKEN}]
            for status, reply in [(200, wrong), (200, dict(envelope({}), status="incomplete")), (401, {"error": {"message": TOKEN}})]:
                self.reserve_replies.append((status, reply))
                with self.subTest(status=status), self.assertRaises(ValueError) as error:
                    codex_provider.complete("private", MODEL, schema)
                self.assertNotIn(TOKEN, str(error.exception))
                self.assertNotIn("private", str(error.exception))

    def test_invalid_backup_data_is_a_visible_failure_with_partial_only_after_primary_quota(self):
        self.reserve_replies.extend([(200, envelope({"bogus": []}))] * 24)
        result = self.run_cli(flags=["--partial-on-quota"])
        self.assertEqual(result["report_type"], "partial")
        self.assertEqual(result["attempted_openai_reserve"]["reason"], "review_failed")
        self.assertIn("полный отчёт получить не удалось", render_report(result))
        self.assertNotIn("run", result)

    def test_invalid_backup_key_preserves_verified_partial_and_hides_the_remote_error_message(self):
        self.reserve_replies.append((401, {"error": {"message": TOKEN}}))
        result = self.run_cli(flags=["--partial-on-quota"])
        self.assertEqual(result["report_type"], "partial")
        self.assertEqual(result["attempted_openai_reserve"], {"model": MODEL, "reason": "review_failed"})
        self.assertEqual(len([p for p, _, _ in self.events if p == "/v1/responses"]), 1)
        self.assertNotIn(TOKEN, self.out.read_text())
        self.assertNotIn(TOKEN, render_report(result))
        for receipt in ({}, {"model": MODEL, "reason": []}, {"model": "<b>", "reason": "review_failed"}):
            with self.subTest(receipt=receipt), self.assertRaisesRegex(ValueError, "Неизвестный резерв"):
                render_report(dict(result, attempted_openai_reserve=receipt))

    def test_prepaid_credit_exhaustion_on_402_or_429_produces_local_report_without_a_retry(self):
        for provider in ("openai", "gemini"):
            for status in (402, 429):
                with self.subTest(provider=provider, status=status):
                    self.events.clear()
                    self.reserve_replies.append((status, {"error": {"code": "credit_balance_exhausted"}}))
                    env = {"REVIEW_PROVIDER": "openai", "REVIEW_MODEL": MODEL} if provider == "openai" else {}
                    result = self.run_cli(flags=["--partial-on-quota"], **env)
                    self.assertEqual(len([p for p, _, _ in self.events if p == "/v1/responses"]), 1)
                    self.assertEqual(result["report_type"], "partial")
                    self.assertEqual(result["reason"], provider + "_quota")
                    if provider == "gemini":
                        self.assertEqual(result["attempted_openai_reserve"]["reason"], "credit_balance_exhausted")
                    else:
                        self.assertNotIn("attempted_openai_reserve", result)
