"""Print the public BCC Markdown as an ordinary text-layer PDF for a format transfer check."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from reviewer.pdf_output import write_pdf


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("markdown", type=Path)
    cli.add_argument("pdf", type=Path)
    cli.add_argument("--marked", default="marked", help="Executable for the Marked Markdown converter")
    args = cli.parse_args()
    body = subprocess.run([args.marked, "--gfm"], input=args.markdown.read_text(encoding="utf-8"),
                          text=True, capture_output=True, check=True).stdout
    style = """
    @page { size: A4; margin: 15mm 16mm; }
    body { color: #1c2327; font: 10pt/1.42 Arial, sans-serif; }
    h1, h2, h3, h4 { break-after: avoid; line-height: 1.2; margin: 1em 0 .35em; }
    h1 { font-size: 21pt; } h2 { font-size: 16pt; }
    p, ul, ol { margin: .4em 0 .65em; }
    table { width: 100%; border-collapse: collapse; break-inside: avoid; font-size: 8.6pt; }
    tr { break-inside: avoid; } th, td { border: .7pt solid #626970; padding: 4pt; vertical-align: top; }
    th { text-align: left; } pre, code { font-family: Menlo, monospace; font-size: .9em; }
    pre { padding: 5pt 7pt; border-left: 2pt solid #8895a1; background: #f2f3f5; white-space: pre-wrap; break-inside: avoid; }
    """
    write_pdf(args.pdf, '<!doctype html><html lang="ru"><meta charset="utf-8"><title>ТЗ BCC</title>'
              + "<style>" + style + "</style><body>" + body + "</body></html>")


if __name__ == "__main__":
    main()
