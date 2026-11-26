"""Generate two one-line revisions of the checkpointed BCC specification."""

import argparse
from hashlib import sha256
from pathlib import Path
import sys

from eval.make_bcc_aligned import make_aligned


def make_variants(source):
    january = make_aligned(source)
    lines = source.splitlines(keepends=True)
    old = "по дате совершения покупки".encode()
    new = "по дате проведения покупки".encode()
    if lines[37].count(old) != 1:
        raise ValueError("Ожидалась одна исходная формулировка в строке 38")
    lines[37] = lines[37].replace(old, new)
    return {"january.md": january, "february.md": b"".join(lines)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    try:
        variants = make_variants(args.source.read_bytes())
        for name in variants:
            if (args.output_dir / name).exists():
                raise ValueError(f"Редакция уже существует: {name}")
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, content in variants.items():
            target = args.output_dir / name
            with target.open("xb") as output:
                output.write(content)
            print(f"SHA-256 {sha256(content).hexdigest()}  {target}")
    except (OSError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
