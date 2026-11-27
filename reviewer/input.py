"""Load a text document, retaining source pages for text-layer PDFs."""

from hashlib import sha256
from io import BytesIO
import re

from .word_input import docx_lines


INPUT_LIMITS = {".md": 120_000, ".txt": 120_000, ".pdf": 16_000_000, ".docx": 16_000_000}
CODE_FONT = re.compile(r"mono|courier|menlo|consolas|fixed|sourcecode|cascadiacode", re.I)
JSON_KEY = r'"(?:[^"\\]|\\.)*"\s*:'
JSON_START = re.compile(
    rf'^\s*(?:\{{\s*{JSON_KEY}|(?:\[\s*)+(?:\{{\s*(?:{JSON_KEY}|\}})|\]|"|-?\d|true\b|false\b|null\b))'
)


def pdf_json_blocks(rows, code_lines, first):
    """Identify standalone typeset containers while retaining their physical line addresses."""
    tokens = re.compile(r'"(?:\\.|[^"\\])*"|[{}\[\]]')
    spans = []
    next_line = 0
    for start, text in enumerate(rows):
        if start < next_line or not code_lines[start] or not text.lstrip().startswith(("{", "[")):
            continue
        expected = []
        done = False
        for end in range(start, min(start + 64, len(rows))):
            if not code_lines[end]:
                break
            for token in tokens.finditer(rows[end]):
                char = token[0]
                if char.startswith('"'):
                    continue
                if char in ("{", "["):
                    expected.append("}" if char == "{" else "]")
                    continue
                if not expected or expected.pop() != char:
                    done = True
                    break
                if not expected:
                    if not rows[end][token.end():].strip() and JSON_START.match("\n".join(rows[start:end + 1])):
                        spans.append({"start": first + start, "end": first + end})
                        next_line = end + 1
                    done = True
                    break
            if done:
                break
    return spans


def table_text(rows):
    width = max((len(row) for row in rows), default=0)
    if not width:
        return []
    result = []
    for i, row in enumerate(rows):
        cells = [" ".join((text or "").split()) for text in row]
        result.append("| " + " | ".join(cells) + " |")
        if i == 0:
            result.append("| " + " | ".join("---" for _ in range(width)) + " |")
    return result


def pdf_lines(raw):
    try:
        import pdfplumber
        from pdfplumber.utils.exceptions import PdfminerException
    except ImportError as exc:
        raise ValueError("Для входного PDF установите зависимости: python3 -m pip install -r requirements.txt") from exc

    lines = []
    pages, examples = [], []
    try:
        document = pdfplumber.open(BytesIO(raw))
    except PdfminerException as exc:
        raise ValueError("PDF повреждён или требует пароль") from exc
    with document:
        for number, page in enumerate(document.pages, 1):
            if not page.chars:
                if page.images or page.curves or page.lines or page.rects:
                    raise ValueError(f"На странице {number} нет текстового слоя. Подготовьте PDF с OCR.")
                continue
            tables = page.find_tables()
            bounds = [table.bbox for table in tables]

            def outside_tables(obj):
                if obj["object_type"] != "char":
                    return True
                x = (obj["x0"] + obj["x1"]) / 2
                y = (obj["top"] + obj["bottom"]) / 2
                return not any(left <= x < right and top <= y < bottom for left, top, right, bottom in bounds)

            chunks = [(row["top"], row["x0"], [row["text"]],
                       all(CODE_FONT.search(c.get("fontname") or "") for c in row["chars"] if c["text"].strip()))
                      for row in page.filter(outside_tables).extract_text_lines(return_chars=True)]
            chunks.extend((table.bbox[1], table.bbox[0], table_text(table.extract()), False) for table in tables)
            rows = [(line, code) for _, _, values, code in sorted(chunks) for line in values if line.strip()]
            page_lines = [line for line, _ in rows]
            if not page_lines:
                raise ValueError(f"Не удалось извлечь содержимое страницы {number}")
            start = len(lines) + 1
            examples.extend(pdf_json_blocks(page_lines, [code for _, code in rows], start))
            lines.extend(page_lines)
            pages.append({"page": number, "start": start, "end": len(lines)})
            lines.append("")
        count = len(document.pages)
    if not lines:
        raise ValueError("В PDF нет читаемого текста")
    return lines[:-1], {"page_count": count, "pages": pages, "pdf_json_blocks": examples,
                       "extraction": "pdfplumber · текстовый слой и таблицы с границами"}


def load_document(path):
    suffix = path.suffix.lower()
    limit = INPUT_LIMITS.get(suffix, 120_000)
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    lines, metadata = extract_document(raw, suffix)
    return lines, sha256(raw).hexdigest(), metadata


def extract_document(raw, suffix):
    limit = INPUT_LIMITS.get(suffix, 120_000)
    if len(raw) > limit:
        raise ValueError(f"Превышен предел размера входного файла: {limit} байт")
    if suffix in {".pdf", ".docx"}:
        lines, metadata = (pdf_lines if suffix == ".pdf" else docx_lines)(raw)
        if len("\n".join(lines).encode()) > 120_000:
            raise ValueError("Извлечённый текст превышает 120 000 байт")
    else:
        lines = raw.decode("utf-8-sig").splitlines()
        metadata = {}
    return lines, metadata
