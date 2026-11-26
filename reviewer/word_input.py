"""Extract reviewable Word text and tables in order, with addresses in the original."""

from io import BytesIO
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile


WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
VALUE = WORD + "val"
XML_LIMIT = 4_000_000


class DocumentTree(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ValueError("DOCX содержит неподдерживаемое объявление XML")


def paragraph_text(node):
    tokens = {WORD + "tab": "\t", WORD + "br": "\n", WORD + "cr": "\n"}
    return "".join((part.text or "") if part.tag == WORD + "t" else tokens.get(part.tag, "")
                   for part in node.iter())


def docx_lines(raw):
    try:
        with ZipFile(BytesIO(raw)) as archive:
            matches = [p for p in archive.infolist() if p.filename == "word/document.xml"]
            if len(matches) != 1 or matches[0].file_size > XML_LIMIT:
                raise ValueError("DOCX должен содержать один раздел word/document.xml размером до 4 МБ")
            with archive.open(matches[0]) as part:
                xml = part.read(XML_LIMIT + 1)
            if len(xml) > XML_LIMIT:
                raise ValueError("DOCX превышает предел размера распакованного текста")
            root = ET.fromstring(xml, parser=ET.XMLParser(target=DocumentTree()))
    except (BadZipFile, ET.ParseError, RuntimeError, NotImplementedError) as exc:
        raise ValueError("DOCX повреждён или защищён паролем") from exc
    body = root.find(WORD + "body")
    if body is None:
        raise ValueError("DOCX не содержит основного текста")
    unsupported = {"del", "ins", "moveFrom", "moveTo", "altChunk", "drawing", "pict",
                   "txbxContent", "footnoteReference", "endnoteReference", "object", "sdt", "fldSimple", "fldChar", "instrText"}
    unsupported_tags = {WORD + tag for tag in unsupported}
    if any(part.tag in unsupported_tags for part in body.iter()):
        raise ValueError("DOCX содержит правки, поля или встроенные элементы. Примите правки и экспортируйте документ в PDF с текстовым слоем.")
    lines, locations = [], []

    def extend(parts, address):
        first = len(lines) + 1
        lines.extend(parts)
        if len(lines) >= first:
            locations.append({"start": first, "end": len(lines), "label": address})

    json_depth = 0
    previous_paragraph = ""

    def finish_json():
        lines.append("```")
        locations[-1]["end"] = len(lines)

    para_no = table_no = 0
    for node in body:
        if node.tag == WORD + "p":
            para_no += 1
            text = paragraph_text(node)
            style = node.find(f"{WORD}pPr/{WORD}pStyle")
            name = style.get(VALUE, "") if style is not None else ""
            heading = re.fullmatch(r"(?:Heading|Заголовок)\s*([1-6])", name, re.IGNORECASE)
            prefix = "#" * int(heading[1]) + " " if heading else "# " if name == "Title" else ""
            parts = text.splitlines() or [""]
            if parts[0].strip():
                parts[0] = prefix + parts[0]
            if json_depth and prefix:
                finish_json()
                json_depth = 0
            begins_json = (not json_depth and re.search(r"\bJSON\b", previous_paragraph, re.I)
                           and text.lstrip().startswith(("{", "[")))
            if begins_json or json_depth:
                if begins_json:
                    parts.insert(0, "```json")
                # Delimit only the explicitly introduced example. The real JSON
                # parser later diagnoses syntax and reports the source column.
                structure = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
                json_depth += sum(structure.count(char) for char in "{[") - sum(structure.count(char) for char in "}]")
                if json_depth <= 0:
                    parts.append("```")
                    json_depth = 0
            extend(parts, f"Абзац {para_no}")
            if text.strip():
                previous_paragraph = text
        elif node.tag == WORD + "tbl":
            if json_depth:
                finish_json()
                json_depth = 0
            previous_paragraph = ""
            table_no += 1
            rows = node.findall(WORD + "tr")
            if node.find(".//" + WORD + "tbl") is not None:
                raise ValueError("DOCX содержит вложенную таблицу. Экспортируйте документ в PDF с текстовым слоем.")
            for i, row in enumerate(rows, 1):
                cells = []
                for cell in row.findall(WORD + "tc"):
                    if any(cell.find(f"{WORD}tcPr/{WORD}{tag}") is not None for tag in ("gridSpan", "vMerge", "hMerge")):
                        raise ValueError("DOCX содержит объединённые ячейки. Экспортируйте документ в PDF с текстовым слоем.")
                    cells.append(" ".join(" ".join(paragraph_text(p).split()) for p in cell.findall(WORD + "p")).strip())
                if cells:
                    rendered = ["| " + " | ".join(cells) + " |"]
                    if i == 1:
                        rendered.append("| " + " | ".join("---" for _ in cells) + " |")
                    extend(rendered, f"Таблица {table_no}, строка {i}")
            lines.append("")
    if json_depth:
        finish_json()
    if not any(line.strip() for line in lines):
        raise ValueError("DOCX не содержит читаемого текста")
    return lines, {"extraction": "DOCX · абзацы и таблицы основного текста", "locations": locations}
