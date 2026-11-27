import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from reviewer.settings import load_project_env


class LocalConfigurationTests(TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / ".env"
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_read_only_project_settings_and_keep_the_shell_override(self):
        self.file.write_text("export REVIEW_PROVIDER=gemini\nREVIEW_MODEL=gemini-example\n"
                             "GEMINI_API_KEY='sample#key' # comment\nPATH=/tmp\n")
        os.environ["REVIEW_PROVIDER"] = "codex"
        load_project_env(self.file)
        self.assertEqual(dict(os.environ), {"REVIEW_PROVIDER":"codex", "GEMINI_API_KEY":"sample#key"})

    def test_template_values_and_empty_shell_setting(self):
        self.file.write_bytes((Path(__file__).resolve().parents[1] / ".env.example").read_bytes())
        os.environ["GEMINI_API_KEY"] = "external"
        load_project_env(self.file)
        self.assertEqual(os.environ["REVIEW_PROVIDER"], "gemini")
        self.assertEqual(os.environ["GEMINI_API_KEY"], "external")
        self.assertEqual(os.environ["REVIEW_MODEL"], "gemini-3.5-flash-lite")
