import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from reviewer import codex_provider, gemini_provider
from reviewer.__main__ import main
from reviewer.contract import REVIEW, validate_shape
from reviewer.full import call_stage


MODEL = "gemini-3.5-flash-lite"
FAKE_KEY = "local-fake-unit-test-key"


def response(value, *, reason="STOP"):
    body = {"candidates": [{"finishReason": reason, "content": {"parts": [
        {"text": "internal note", "thought": True}, {"text": json.dumps(value, ensure_ascii=False)}]}}],
            "usageMetadata": {"promptTokenCount": 110, "cachedContentTokenCount": 30,
                              "candidatesTokenCount": 16, "thoughtsTokenCount": 7}}
    return io.BytesIO(json.dumps(body).encode())


def http_error(code, text):
    return HTTPError("https://generativelanguage.googleapis.com/", code, "error", {},
                     io.BytesIO(json.dumps({"error": {"message": text}}).encode()))


class GeminiProviderTests(unittest.TestCase):
    def test_transient_unavailability_retries_once_while_quota_and_long_waits_do_not(self):
        schema = {"type": "object"}
        with patch.object(gemini_provider, "sleep") as delay, patch.object(
                gemini_provider, "urlopen", side_effect=[http_error(503, "busy"), response({})]) as send:
            result, _ = gemini_provider.complete("check", MODEL, "low", schema)
            self.assertEqual(result, {})
            self.assertEqual(send.call_count, 2)
            delay.assert_called_once_with(2)
        for code, wait in ((429, "1"), (503, "60")):
            error = http_error(code, "busy")
            error.headers["Retry-After"] = wait
            with patch.object(gemini_provider, "urlopen", side_effect=error) as send:
                with self.assertRaisesRegex(ValueError, f"HTTP {code}"):
                    gemini_provider.complete("check", MODEL, "low", schema)
                self.assertEqual(send.call_count, 1)

    def setUp(self):
        env = patch.dict(os.environ, {"REVIEW_PROVIDER": "gemini", "GEMINI_API_KEY": FAKE_KEY}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_witness_values_use_google_union_syntax_while_nullable_answers_keep_json_null(self):
        from reviewer.witness_vm import VALUE
        value = gemini_provider.wire_schema(VALUE)["properties"]["value"]
        self.assertEqual(value, {"anyOf": [{"type": "integer"}, {"type": "string"}]})
        self.assertEqual(VALUE["properties"]["value"], {"type": ["integer", "string"]})
        nullable = {"type": ["string", "null"]}
        self.assertEqual(gemini_provider.wire_schema(nullable), nullable)

    def test_wire_request_and_cli_passport(self):
        with tempfile.TemporaryDirectory() as directory:
            source, report = Path(directory) / "new.txt", Path(directory) / "report.json"
            source.write_text("Архивная заявка получает состояние CLOSED.", encoding="utf-8")
            with patch("reviewer.gemini_provider.urlopen", return_value=response({"conflicts": []})) as transport, \
                    patch("sys.argv", ["reviewer", str(source), str(report), "--conflicts-only", "--effort", "low"]):
                main()
            result = json.loads(report.read_text())
        self.assertEqual((result["run"]["provider"], result["run"]["model"]), ("gemini-api", MODEL))
        self.assertEqual(result["run"]["usage"], {"input_tokens": 110, "cached_input_tokens": 30,
                                                  "reasoning_output_tokens": 7, "output_tokens": 23})
        req = transport.call_args.args[0]
        self.assertEqual(req.full_url, gemini_provider.ENDPOINT + MODEL + ":generateContent")
        self.assertEqual(req.get_header("X-goog-api-key"), FAKE_KEY)
        self.assertNotIn(FAKE_KEY, req.data.decode())
        self.assertNotIn(FAKE_KEY, json.dumps(result))
        body = json.loads(req.data)
        self.assertEqual(body["generationConfig"], {"responseMimeType": "application/json",
                                                   "responseJsonSchema": REVIEW,
                                                   "thinkingConfig": {"thinkingLevel": "LOW"}})
        self.assertIn("Архивная заявка", body["contents"][0]["parts"][0]["text"])
        self.assertNotIn("tools", body)

    def test_schema_rejection_retries_on_same_provider_and_keeps_source_validation(self):
        shape = {"type": "object", "properties": {"quote": {"type": "string"}},
                 "required": ["quote"], "additionalProperties": False}
        def verify(result):
            if result["quote"] != "Архив закрыт":
                raise ValueError("Цитата должна совпадать с исходной строкой")
        with patch("reviewer.gemini_provider.urlopen", side_effect=[
                http_error(400, "INVALID_ARGUMENT: schema has too many states"),
                response({"quote": "Открыт"}), response({"quote": "Архив закрыт"})]) as wire:
            result, attempts = call_stage("Сверка", "Проверь источник", {"text": "Архив закрыт"},
                                          shape, verify, MODEL, None, "engine")
        self.assertEqual(result, {"quote": "Архив закрыт"})
        self.assertEqual([event["status"] for event in attempts], ["rejected", "accepted"])
        requests = [call.args[0] for call in wire.call_args_list]
        self.assertEqual(len({r.full_url for r in requests}), 1)
        fallback = json.loads(requests[1].data)
        self.assertNotIn("responseJsonSchema", fallback["generationConfig"])
        self.assertIn('"required":["quote"]', fallback["contents"][0]["parts"][1]["text"])
        self.assertIn("Исправь ошибку", json.loads(requests[2].data)["contents"][0]["parts"][0]["text"])

    def test_quota_key_and_transport_errors_are_redacted_and_never_routed_to_codex(self):
        with (patch("reviewer.codex_provider.subprocess.run", side_effect=AssertionError("unexpected Codex call")),
              patch.object(gemini_provider, "sleep")):
            for code, expected in [(400, "HTTP 400"), (401, "HTTP 401"), (403, "HTTP 403"),
                                   (429, "HTTP 429 RESOURCE_EXHAUSTED"), (503, "HTTP 503")]:
                with self.subTest(code=code), patch("reviewer.gemini_provider.urlopen", side_effect=http_error(
                        code, "Confidential user text and " + FAKE_KEY)) as wire:
                    with self.assertRaises(ValueError) as error:
                        codex_provider.complete("private", MODEL)
                    self.assertEqual(str(error.exception), "Gemini API: " + expected)
                    self.assertEqual(wire.call_count, 2 if code == 503 else 1)
            with patch.dict(os.environ, {"GEMINI_API_KEY": ""}), patch("reviewer.gemini_provider.urlopen") as wire:
                with self.assertRaisesRegex(ValueError, "ключ не настроен"):
                    codex_provider.complete("private", MODEL)
                wire.assert_not_called()
            with patch("reviewer.gemini_provider.urlopen", return_value=response({}, reason="MAX_TOKENS")):
                with self.assertRaisesRegex(ValueError, "не завершила"):
                    codex_provider.complete("private", MODEL)

    def test_provider_specific_checkpoints_cannot_be_reused_for_another_transport(self):
        def verify(value):
            validate_shape(value, REVIEW, "Проверка")
        with tempfile.TemporaryDirectory() as tmp, patch("reviewer.codex_provider.complete",
                                                         return_value=({"conflicts": []}, {})) as provider:
            args = ("Проверка", "Инструкция", {}, REVIEW, verify, "shared-model-name", Path(tmp), "same-engine")
            call_stage(*args)
            with patch.dict(os.environ, {"REVIEW_PROVIDER": "codex"}):
                self.assertEqual(codex_provider.default_model(), "gpt-6.2-astra")
                _, fresh = call_stage(*args)
                _, saved = call_stage(*args)
            self.assertFalse(fresh[0].get("restored", False))
            self.assertTrue(saved[0]["restored"])
            self.assertEqual(provider.call_count, 2)
