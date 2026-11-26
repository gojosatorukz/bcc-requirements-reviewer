from collections import defaultdict
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from reviewer.contract import source_lines
from reviewer.executable import verify_source
from reviewer.input import load_document
from reviewer.render import render_source
from reviewer.segments import segment
from reviewer.word_input import WORD, docx_lines


def word_file(body):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml",
            f'<w:document xmlns:w="{WORD[1:-1]}"><w:body>{body}</w:body></w:document>')
    return buffer.getvalue()


def paragraph(value):
    return f"<w:p><w:r><w:t>{value}</w:t></w:r></w:p>"


def row(*cells):
    return "<w:tr>" + "".join("<w:tc>" + paragraph(c) + "</w:tc>" for c in cells) + "</w:tr>"


class WordInputTests(unittest.TestCase):
    def test_body_order_table_header_evidence_and_original_docx_address(self):
        raw = word_file('<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Тариф</w:t></w:r></w:p>'
                        + "<w:tbl>" + row("Сумма", "Статус") + row("4000", "ожидает") + "</w:tbl>"
                        + "<w:p><w:r><w:t>GET</w:t><w:tab/><w:t>/state</w:t><w:br/><w:t>200</w:t></w:r></w:p>")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Тариф.DOCX"
            source.write_bytes(raw)
            lines, digest, metadata = load_document(source)
            report = {"source": [{"line": i, "text": text} for i, text in enumerate(lines, 1) if text.strip()],
                      "run": {**metadata, "input_lines": len(lines), "input_sha256": digest}}
            self.assertEqual(verify_source(report, source), sha256(raw).hexdigest())
            self.assertEqual(lines, ["# Тариф", "| Сумма | Статус |", "| --- | --- |",
                                     "| 4000 | ожидает |", "", "GET\t/state", "200"])
            self.assertIn({"start": 4, "end": 4, "kind": "строка таблицы", "section": "Тариф",
                           "header": 2}, segment(lines))
            sources = source_lines(report)
            html = render_source(sources, defaultdict(list), locations=metadata["locations"])
            self.assertIn('Таблица 1, строка 2</small>| 4000 | ожидает |', html)
            report["run"]["locations"] = [dict(start=1, end=999, label="другая строка")]
            with self.assertRaisesRegex(ValueError, "адрес"):
                source_lines(report)
            with self.assertRaisesRegex(ValueError, "абзацев"):
                verify_source(report, source)

    def test_unknown_scope_is_explicit(self):
        for fragment in ('<w:ins>' + paragraph('Условие') + '</w:ins>',
                         '<w:p><w:r><w:drawing/></w:r></w:p>',
                         '<w:tbl><w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr></w:tc></w:tr></w:tbl>'):
            with self.subTest(fragment=fragment), self.assertRaisesRegex(ValueError, "PDF"):
                docx_lines(word_file(fragment))
        with self.assertRaisesRegex(ValueError, "повреждён"):
            docx_lines(b"not a word document")
