"""Print the same offline report as PDF using a local Chromium browser."""

import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory


def write_pdf(path, html):
    candidates = [os.environ.get("REVIEW_CHROME"), shutil.which("chromium"), shutil.which("google-chrome"),
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    browser = next((p for p in candidates if p and os.access(p, os.X_OK)), None)
    if browser is None:
        raise ValueError("Для записи PDF нужен Chrome/Chromium. Укажите исполняемый файл в REVIEW_CHROME.")
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".print-", dir=path.parent) as tmp:
        directory = Path(tmp).resolve()
        source, output = directory / "report.html", directory / "report.pdf"
        # Expand the source ledger and all audit details in the printed edition.
        html = re.sub(r"<details(?=[\s>])", "<details open", html)
        source.write_text(html, encoding="utf-8")
        command = [browser, "--headless", "--disable-gpu", "--disable-background-networking", "--no-first-run",
                   "--no-default-browser-check", "--no-pdf-header-footer", f"--user-data-dir={directory / 'profile'}",
                   f"--print-to-pdf={output}", source.as_uri()]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=120)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ValueError("Chrome не смог завершить печать отчёта") from exc
        if not output.is_file() or output.stat().st_size < 5:
            raise ValueError("Chrome не создал файл отчёта")
        output.replace(path)
