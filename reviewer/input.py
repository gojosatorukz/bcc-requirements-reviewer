"""Load a text document, retaining source pages for text-layer PDFs."""

from hashlib import sha256
from io import BytesIO


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
    pages = []
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

            chunks = [(row["top"], row["x0"], [row["text"]])
                      for row in page.filter(outside_tables).extract_text_lines(return_chars=False)]
            chunks.extend((table.bbox[1], table.bbox[0], table_text(table.extract())) for table in tables)
            page_lines = [line for _, _, values in sorted(chunks) for line in values if line.strip()]
            if not page_lines:
                raise ValueError(f"Не удалось извлечь содержимое страницы {number}")
            start = len(lines) + 1
            lines.extend(page_lines)
            pages.append({"page": number, "start": start, "end": len(lines)})
            lines.append("")
        count = len(document.pages)
    if not lines:
        raise ValueError("В PDF нет читаемого текста")
    return lines[:-1], {"page_count": count, "pages": pages, "extraction": "pdfplumber · текстовый слой и таблицы с границами"}


def load_document(path):
    pdf = path.suffix.lower() == ".pdf"
    limit = 16_000_000 if pdf else 120_000
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"Превышен предел размера входного файла: {limit} байт")
    if pdf:
        lines, metadata = pdf_lines(raw)
        if len("\n".join(lines).encode()) > 120_000:
            raise ValueError("Извлечённый текст превышает 120 000 байт")
    else:
        lines = raw.decode("utf-8-sig").splitlines()
        metadata = {}
    return lines, sha256(raw).hexdigest(), metadata
