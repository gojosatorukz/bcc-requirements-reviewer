"""Reproduce the preregistered live experiment, refusing to overwrite saved results."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import subprocess
import sys

from audit import BASE_SHA, MODEL, ORIGINAL_SHA


FIRST = "Для покупок на стыке месяцев завели отдельный счётчик „Переходящие“. Это точно."
SECOND = ("Да, только в „Переходящие“. Если по времени Алматы месяц покупки и месяц проведения "
          "различаются, после проведения вся сумма идёт туда, ни один месячный счётчик не меняется. "
          "Когда месяцы совпадают, вся сумма идёт в этот месяц, в „Переходящие“ ничего не добавляем.")


def experiment(engine, original, out):
    baseline = engine / "runs/bcc-submission/review.json"
    examples = engine / "eval/freeform-owner"
    if sha256(original.read_bytes()).hexdigest() != ORIGINAL_SHA or sha256(baseline.read_bytes()).hexdigest() != BASE_SHA:
        raise ValueError("Эксперимент привязан к официальному ТЗ и сохранённому полному отчёту")
    out.mkdir(exist_ok=False, parents=True)
    shared = ["--finding", "D-01", "--baseline-sha256", BASE_SHA, "--model", MODEL]
    first = out / "turn-1.json"
    second = out / "turn-2.json"
    for answer, target, prior in ((FIRST, first, []), (SECOND, second, ["--previous", first])):
        subprocess.run([sys.executable, "-m", "reviewer.clarify", baseline, *shared,
                        "--answer", answer, "--out", target, *prior], cwd=engine, check=True)

    def recheck(name, source, turn):
        subprocess.run([sys.executable, "-m", "reviewer.recheck", baseline, source,
                        out / f"{name}.json", *shared, "--clarification", turn], cwd=engine, check=True)

    cases = [
        ("partial-complete", examples / "complete.md", first),
        ("ready-original", original, second),
        ("ready-february", engine / "eval/owner-decision/february.md", second),
        ("ready-complete", examples / "complete.md", second),
        ("ready-wrong-same-month", examples / "wrong-same-month.md", second),
    ]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(recheck, *case) for case in cases]
        for future in futures:
            future.result()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("output", type=Path, help="Новый каталог")
    parser.add_argument("--engine", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    experiment(args.engine.resolve(), args.original.resolve(), args.output.resolve())
