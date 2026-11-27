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

from reviewer import codex_provider, gemini_provider, lifecycle, openai_provider, serve
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
        self.retry_headers = {"Retry-After": "0"}
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
                for name, value in case.retry_headers.items():
                    self.send_header(name, value)
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

    def test_small_calls_do_not_reserve_the_maximum_output_and_respect_an_explicit_ceiling(self):
        with patch.dict(os.environ, {"REVIEW_PROVIDER": "openai", "OPENAI_API_KEY": TOKEN}, clear=True):
            for length, expected in ((50, 4096), (10001, 8192), (35001, 16384)):
                self.reserve_replies.append((200, envelope({"answer": "ok"})))
                codex_provider.complete("x" * length, MODEL, record(answer={"type": "string"}))
                self.assertEqual(self.events[-1][2]["max_output_tokens"], expected)
            with patch.dict(os.environ, {"OPENAI_MAX_OUTPUT_TOKENS": "2048"}):
                self.assertEqual(openai_provider.output_budget("x" * 50000), 2048)
            with patch.dict(os.environ, {"OPENAI_MAX_OUTPUT_TOKENS": "invalid"}), self.assertRaises(ValueError):
                openai_provider.output_budget("")

    def run_cli(self, flags=(), workers=1, **environment):
        parallel = [] if workers is None else ["--workers", str(workers)]
        with patch.dict(os.environ, {**RESERVE, **environment}, clear=True), patch("sys.argv", [
                "reviewer", str(self.source), str(self.out), *parallel, *flags]):
            main()
        return json.loads(self.out.read_bytes())

    def test_full_rerun_over_local_http_with_structured_output_and_actual_model_receipt(self):
        result = self.run_cli(workers=None)
        validate_report(result)
        run = result["run"]
        self.assertEqual((run["provider"], run["model"], run["response_models"]),
                         ("openai-responses", MODEL, [SNAPSHOT]))
        self.assertEqual(run["max_workers"], 1)
        self.assertEqual(run["fallback_from"], {"provider": "gemini-api", "model": "gemini-3.5-flash-lite",
                                                "reason": "HTTP 429"})
        self.assertEqual(result["registry"][0]["statement"], RULE)
        self.assertEqual(self.events[0][1]["x-goog-api-key"], "google-test")
        self.assertNotIn("authorization", self.events[0][1])
        responses = [event for event in self.events if event[0] == "/v1/responses"]
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
            self.reserve_replies.append((200, {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}))
            with self.assertRaisesRegex(ValueError, "4096 токенов") as cutoff:
                codex_provider.complete("private", MODEL, schema)
            self.assertNotIsInstance(cutoff.exception, openai_provider.QuotaExceeded)

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

    def direct_worker_env(self):
        # Site and CLI use independent real subprocesses. Redirect the transport only
        # inside this test environment and reject any off-host network connection.
        (self.source.parent / "sitecustomize.py").write_text(
            f"from reviewer import openai_provider\nopenai_provider.ENDPOINT = {openai_provider.ENDPOINT!r}\n")
        return {"PYTHONPATH": str(self.source.parent) + os.pathsep + str(serve.ROOT),
                "REVIEW_PROVIDER": "openai", "REVIEW_MODEL": MODEL, "OPENAI_API_KEY": TOKEN,
                "HTTPS_PROXY": "http://127.0.0.1:1", "NO_PROXY": "127.0.0.1"}

    def test_direct_public_worker_writes_both_partial_artifacts_after_one_paid_stage(self):
        self.source.write_text("Порог: TBD\n", encoding="utf-8")
        html = self.out.with_suffix(".html")
        progress = []
        env = self.direct_worker_env()
        for status, failure in ((429, {"error": {"code": "credit_balance_exhausted"}}),
                                (200, {"status": "failed", "error": {"code": "insufficient_quota"}})):
            with self.subTest(status=status), patch.dict(os.environ, env, clear=True):
                self.events.clear()
                self.reserve_replies.extend([(200, envelope({"conflicts": []})), (status, failure)])
                self.assertEqual(serve.cli_worker("full", self.source, None, self.out, html, MODEL, progress.append), "partial")
                report = json.loads(self.out.read_bytes())
                self.assertEqual(report["reason"], "openai_quota")
                self.assertEqual(report["findings"][0]["evidence"], [{"line": 1, "quote": "Порог: TBD"}])
                self.assertIn(report["findings"][0]["question"], html.read_text())
                self.assertNotIn("run", report)
                self.assertEqual(len(self.events), 2)

    def test_direct_worker_reports_stage_and_known_http_code_without_response_content(self):
        for supplied, expected in [("invalid_json_schema", "http_400_invalid_json_schema"), (TOKEN, "http_400")]:
            with self.subTest(code=expected), patch.dict(os.environ, self.direct_worker_env(), clear=True):
                self.reserve_replies.append((400, {"error":{"code":supplied, "message":TOKEN}}))
                with redirect_stderr(io.StringIO()) as log, self.assertRaises(serve.SafeWorkerError) as failure:
                    serve.cli_worker("full", self.source, None, self.out, self.out.with_suffix(".html"), MODEL, lambda _: None)
                self.assertEqual(log.getvalue(), f"review_stage_error stage=Сценарии code={expected}\n")
                self.assertIn("Сценарии", str(failure.exception))
                self.assertNotIn(TOKEN, str(failure.exception))

    def test_retry_after_and_spend_limit_handling(self):
        model = {"REVIEW_PROVIDER": "openai", "OPENAI_API_KEY": TOKEN}
        with patch.dict(os.environ, model, clear=True), patch("reviewer.openai_provider.sleep") as wait:
            schema = record(answer={"type": "string"})
            for headers, lower in (({"Retry-After": "12"}, 12), ({"retry-after-ms": "500"}, 0.5), ({}, 1)):
                self.retry_headers = headers
                self.reserve_replies.extend([(429, {"error": {"code": "slow_down"}}), (200, envelope({"answer": "ok"}))])
                with self.subTest(headers=headers), redirect_stderr(io.StringIO()) as progress:
                    codex_provider.complete("тест", MODEL, schema)
                    self.assertIn(openai_provider.RETRY_PROGRESS, progress.getvalue())
                    self.assertGreaterEqual(wait.call_args.args[0], lower)
                    self.assertLessEqual(wait.call_args.args[0], lower + 1)
            wait.reset_mock()
            self.retry_headers = {"Retry-After": "45"}
            self.reserve_replies.append((429, {"error": {"code": "rate_limit_exceeded"}}))
            with self.assertRaises(openai_provider.QuotaExceeded):
                codex_provider.complete("тест", MODEL, schema)
            self.retry_headers = {"Retry-After": "0"}
            for payload in ({"code": "project_spend_limit_exceeded"}, {"type": "insufficient_quota"}):
                self.reserve_replies.append((429, {"error": payload}))
                with self.assertRaises(openai_provider.QuotaExceeded) as failure:
                    codex_provider.complete("тест", MODEL, schema)
                self.assertEqual(failure.exception.reason, "insufficient_quota")
            wait.assert_not_called()
