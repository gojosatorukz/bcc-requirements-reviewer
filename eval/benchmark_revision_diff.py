"""Compare revision-diff CPU time with the fixed pre-optimization implementation."""

import argparse
import ast
from difflib import SequenceMatcher
from pathlib import Path
from statistics import median
import subprocess
import sys
from time import perf_counter_ns

BASE = "eef5347baf1427296726db2f103dbd843688ba2b"
ROOT = Path(__file__).resolve().parents[1]


def load_baseline():
    source = subprocess.check_output(["git", "show", f"{BASE}:reviewer/recheck.py"], cwd=ROOT, text=True)
    parsed = ast.parse(source)
    definitions = [item for item in parsed.body if isinstance(item, ast.FunctionDef)
                   and item.name in {"normalized", "make_diff"}]
    namespace = {"SequenceMatcher": SequenceMatcher}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), f"{BASE}:reviewer/recheck.py", "exec"), namespace)
    return namespace["make_diff"]


def fixtures(count):
    line = "При достижении порога клиент получает push."
    original = {i + 1: line for i in range(count)}
    shifted = {i + 11: "При достижении  порога клиент получает push." for i in range(count)}
    middle = dict(original)
    middle[count // 2] = "При достижении порога дохода клиент получает push."
    return (
        ("Повторяющиеся строки, совпадают", original, dict(original)),
        ("Повторяющиеся строки, сдвиг и пробелы", original, shifted),
        ("Одна правка между длинными повторами", original, middle),
        ("Один пробел внутри JSON-значения", {5: '{"message":"a  b"}'}, {8: '{"message":"a b"}'}),
    )


def measure(function, old, new):
    start = perf_counter_ns()
    output = function(old, new)
    return (perf_counter_ns() - start) / 1_000_000, output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=1800)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    if args.rows < 2 or args.repeats < 1:
        parser.error("Rows must be >= 2 and repeats >= 1")
    sys.path.insert(0, str(ROOT))
    from reviewer.recheck import make_diff

    baseline = load_baseline()
    print(f"База {BASE}, строк {args.rows}, повторов {args.repeats}. Медиана, мс. Замер локального diff.")
    print(f'{"Сценарий":<48} {"База":>10} {"Новый":>10}   Блоки (база → новый)')
    for case, old, new in fixtures(args.rows):
        samples = [[], []]
        outputs = [None, None]
        functions = [baseline, make_diff]
        for i in range(args.repeats):
            order = (0, 1) if i % 2 == 0 else (1, 0)
            for index in order:
                elapsed, outputs[index] = measure(functions[index], old, new)
                samples[index].append(elapsed)
        print(f"{case:<48} {median(samples[0]):>10.3f} {median(samples[1]):>10.3f}   "
              f"{len(outputs[0])} → {len(outputs[1])}")


if __name__ == "__main__":
    main()
