"""Assemble the eight competition files from the verified release and original specification."""

import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from reviewer.dialogue_view import render_with_followup
from reviewer.executable import verify_source


def package(source, output):
    release = ROOT / "runs/bcc-jury"
    files = {
        "requirements.md": source,
        "demo.mp4": ROOT / "presentation/bcc-demo.mp4",
        "pitch.pptx": ROOT / "presentation/pitch/razbor-tz-pitch.pptx",
        "pitch.pdf": ROOT / "presentation/pitch/razbor-tz-pitch.pdf",
        **{name: release / name for name in ("review.json", "review.html", "review.pdf")},
    }
    if output.suffix.lower() != ".zip" or output.resolve() in {p.resolve() for p in files.values()}:
        raise ValueError("Укажите отдельный выходной файл ZIP")
    material = {name: path.read_bytes() for name, path in files.items()}
    material["repository.txt"] = b"https://github.com/gojosatorukz/bcc-requirements-reviewer\n"
    hashes = {name: sha256(data).hexdigest() for name, data in material.items()}
    if hashes != json.loads((release / "materials.json").read_bytes()):
        raise ValueError("Файлы расходятся с проверенным комплектом. Проверьте редакцию ТЗ и материалы подачи.")
    report = json.loads(material["review.json"])
    verify_source(report, source)
    expected_html = render_with_followup(material["review.json"], release / "owner/dialogue.json",
                                         release / "owner/wrong-revision.json")
    if expected_html.encode("utf-8") != material["review.html"]:
        raise ValueError("Пересоберите HTML из текущего отчёта, диалога и перепроверки перед упаковкой")

    timestamp = datetime.fromisoformat(report["run"]["started_utc"]).timetuple()[:6]
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as tmp:
        stage = Path(tmp) / "materials.zip"
        with ZipFile(stage, "w") as archive:
            for name in sorted(material):
                member = ZipInfo(name, date_time=timestamp)
                member.create_system = 3
                member.external_attr = 0o100644 << 16
                archive.writestr(member, material[name], compress_type=ZIP_DEFLATED)
        with ZipFile(stage) as archive:
            if archive.testzip() or {n: sha256(archive.read(n)).hexdigest() for n in archive.namelist()} != hashes:
                raise ValueError("Проверка записанного ZIP не прошла")
        stage.replace(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Упаковать проверенную заявку BCC-HUB × MNU")
    parser.add_argument("source", type=Path, help="Скачанный оригинал requirements.md")
    parser.add_argument("output", type=Path, help="Путь к ZIP")
    args = parser.parse_args()
    try:
        package(args.source, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Комплект не собран: {exc}\n")
    print(f"Комплект сохранён: {args.output}")
