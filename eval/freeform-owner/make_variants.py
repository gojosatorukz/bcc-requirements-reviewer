"""Construct the two acceptance fixtures from the byte-exact BCC original."""

import argparse
from hashlib import sha256
from pathlib import Path


ORIGINAL = "e7dae55e5453a250d93e335f5ff9c7f775f4457ccddd333f4a67e47b20d74381"
RULE = (
    "* **Ф-2.** Месяцы совершения и проведения покупки сравниваются по времени Алматы. "
    "При разных месяцах вся сумма проведённой покупки поступает только в счётчик «Переходящие», "
    "месячные счётчики не изменяются. "
)
SAME_MONTH = {
    "complete.md": "При совпадении месяцев вся сумма зачисляется в этот месяц, счётчик «Переходящие» не изменяется. ",
    "wrong-same-month.md": "При совпадении месяцев вся сумма зачисляется и в этот месяц, и в счётчик «Переходящие». ",
}
REST = "Возврат уменьшает расходы месяца исходной покупки. Переводы и снятие наличных исключаются."
EXAMPLE = (
    "| Покупка 31 января в 23:58 на 4 000 ₸ | Провести 1 февраля в 00:03 по Алматы | "
    "Январский и февральский счётчики не изменились, счётчик «Переходящие» вырос на 4 000 ₸ |"
)


def make_variants(original):
    if sha256(original).hexdigest() != ORIGINAL:
        raise ValueError("Ожидалась официальная версия ТЗ BCC")
    original_lines = original.splitlines(keepends=True)
    for name, same_month in SAME_MONTH.items():
        lines = original_lines.copy()
        for number, content in ((38, RULE + same_month + REST), (64, EXAMPLE)):
            old = lines[number - 1]
            lines[number - 1] = content.encode() + old[len(old.rstrip(b"\r\n")):]
        yield name, b"".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name, data in make_variants(args.original.read_bytes()):
        dest = args.output / name
        with dest.open("xb") as stream:
            stream.write(data)
        print(f"{sha256(data).hexdigest()}  {dest}")
