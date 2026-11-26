"""Make the one-line calibration variant of the official BCC requirements."""

import argparse
import hashlib
from pathlib import Path

OFFICIAL_SHA256 = "e7dae55e5453a250d93e335f5ff9c7f775f4457ccddd333f4a67e47b20d74381"
ACCEPTANCE_LINE = 64
ORIGINAL = (
    "| Покупка 31 января в 23:58 на 4 000 ₸ | Провести 1 февраля в 00:03 по Алматы "
    "| Февральский счётчик вырос на 4 000 ₸ |"
).encode("utf-8")
ALIGNED = ORIGINAL.replace("Февральский".encode(), "Январский".encode())


def make_aligned(source: bytes) -> bytes:
    actual_hash = hashlib.sha256(source).hexdigest()
    if actual_hash != OFFICIAL_SHA256:
        raise ValueError(f"Ожидался SHA-256 {OFFICIAL_SHA256}, получен {actual_hash}")
    lines = source.splitlines(keepends=True)
    original_line = lines[ACCEPTANCE_LINE - 1]
    if original_line.rstrip(b"\r\n") != ORIGINAL:
        raise ValueError(f"Неожиданное содержание строки {ACCEPTANCE_LINE}")
    lines[ACCEPTANCE_LINE - 1] = ALIGNED + original_line[len(ORIGINAL):]
    return b"".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    try:
        aligned = make_aligned(args.source.read_bytes())
        with args.output.open("xb") as output:
            output.write(aligned)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    print(f"SHA-256 {hashlib.sha256(aligned).hexdigest()}  {args.output}")


if __name__ == "__main__":
    main()
