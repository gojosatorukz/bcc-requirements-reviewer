"""Verify a website CLI run and its provenance across the actual provider boundary."""

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from reviewer import cloudflare_provider, codex_provider, lifecycle
from reviewer.__main__ import main
from reviewer.full import AXES
from reviewer.full_render import validate_report


ACCOUNT = "a" * 32
TOKEN = "unit-test-workers-ai-only"
FALLBACK = cloudflare_provider.DEFAULT_MODEL
ENV = {"REVIEW_PROVIDER": "gemini", "GEMINI_API_KEY": "test-google",
       "CLOUDFLARE_ACCOUNT_ID": ACCOUNT, "CLOUDFLARE_API_TOKEN": TOKEN,
       "REVIEW_FALLBACK_MODEL": FALLBACK}


def response(value, reason="stop"):
    return io.BytesIO(json.dumps({"choices": [{"finish_reason": reason,
        "message": {"role": "assistant", "content": json.dumps(value, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 8, "completion_tokens": 4}}).encode())


class CloudflareProviderTest(unittest.TestCase):
    def test_unknown_document_is_validated_by_the_full_pipeline_after_real_http_429(self):
        rule = "Кабина возвращается на базу после выгрузки."
        observation = {"role": "требование", "statement": rule, "evidence": 1,
                       "status": "проверяемо", "assessment": "Действие определено."}
        gate = {"status": "можно начинать", "rationale": "Действие описано.", "evidence": [1]}
        outputs = {"rows": {"rows": [{"start": 1, "note": "Возврат.", "items": [observation]}]},
                   "checks": {"checks": [], "skipped": [{"axis": axis, "reason": "Один шаг."}
                                                         for axis in lifecycle.AXES]},
                   "conflicts": {"conflicts": []},
                   "dimensions": {"dimensions": [{"criterion": axis, "assessment": "Действие определено.",
                                                   "evidence": [1]} for axis in AXES],
                                  "findings": [], "reconciled": [], "development": gate, "testing": gate}}
        def cloud(request, timeout):
            self.assertEqual(request.full_url, f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/ai/v1/chat/completions")
            self.assertEqual(request.get_header("Authorization"), "Bearer " + TOKEN)
            self.assertNotIn(TOKEN.encode(), request.data)
            body = json.loads(request.data)
            self.assertEqual(body["model"], FALLBACK)
            schema = json.loads(body["messages"][0]["content"].split("\n")[-1])
            return response(outputs[next(iter(schema["properties"]))])

        # First Gemini stage succeeds, then quota expires. Cloudflare must repeat this stage.
        google = io.BytesIO(json.dumps({"candidates": [{"finishReason": "STOP", "content": {
            "parts": [{"text": '{"conflicts":[]}'}]}}]}).encode())
        limited = HTTPError("https://generativelanguage.googleapis.com/", 429, "limited", {}, io.BytesIO(b"{}"))
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, ENV, clear=True), \
                patch("reviewer.gemini_provider.urlopen", side_effect=[google, limited]) as primary, \
                patch("reviewer.cloudflare_provider.urlopen", side_effect=cloud) as backup:
            source, out = Path(tmp) / "cabin.md", Path(tmp) / "report.json"
            source.write_text(rule, encoding="utf-8")
            with patch("sys.argv", ["reviewer", str(source), str(out), "--workers", "1",
                                    "--checkpoint-dir", str(Path(tmp) / "cache")]):
                main()
            report = json.loads(out.read_bytes())
            validate_report(report)
            self.assertEqual(primary.call_count, 2)
            self.assertEqual(backup.call_count, 5)
            self.assertEqual(report["registry"][0]["statement"], rule)
            self.assertEqual(report["run"]["restored_attempts"], 0)
            self.assertEqual(report["run"]["usage"]["input_tokens"], 40)
            self.assertEqual((report["run"]["model"], report["run"]["provider"]), (FALLBACK, "cloudflare-workers-ai"))
            self.assertEqual(report["run"]["fallback_from"], {"reason": "HTTP 429", "provider": "gemini-api",
                                                            "model": "gemini-3.5-flash-lite"})

    def test_invalid_model_or_input_cannot_trigger_fallback_by_printing_quota_text(self):
        with patch.dict(os.environ, ENV, clear=True), patch.object(cloudflare_provider, "complete") as backup:
            for error in (ValueError("Gemini API: HTTP 429 RESOURCE_EXHAUSTED"), ValueError("Неверная цитата")):
                def reject(model):
                    raise error
                with self.assertRaises(ValueError) as result:
                    codex_provider.with_quota_fallback(reject, "gemini-3.5-flash-lite")
                self.assertIs(result.exception, error)
            backup.assert_not_called()

    def test_invalid_backup_payload_never_creates_a_successful_cli_report(self):
        limited = HTTPError("https://generativelanguage.googleapis.com/", 429, "limited", {}, io.BytesIO(b"{}"))
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, ENV, clear=True), \
                patch("reviewer.gemini_provider.urlopen", side_effect=limited), \
                patch("reviewer.cloudflare_provider.urlopen", side_effect=lambda *a, **kw: response({"wrong": []})):
            source, output = Path(tmp) / "rule.txt", Path(tmp) / "report.json"
            source.write_text("В 12 часов окно открывается.", encoding="utf-8")
            with patch("sys.argv", ["reviewer", str(source), str(output), "--workers", "1"]), \
                    patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit) as result:
                main()
            self.assertEqual(result.exception.code, 1)
            self.assertFalse(output.exists())

    def test_truncated_output_and_provider_errors_never_become_reports(self):
        with patch.dict(os.environ, ENV):
            with patch.object(cloudflare_provider, "urlopen", return_value=response({}, "length")):
                with self.assertRaisesRegex(ValueError, "завершённый ответ JSON"):
                    cloudflare_provider.complete("private", FALLBACK, "medium", {})
            error = HTTPError("https://api.cloudflare.com/", 403, TOKEN, {}, io.BytesIO(TOKEN.encode()))
            with patch.object(cloudflare_provider, "urlopen", side_effect=error):
                with self.assertRaises(ValueError) as result:
                    cloudflare_provider.complete("private", FALLBACK, "medium", {})
                self.assertEqual(str(result.exception), "Workers AI: HTTP 403")
