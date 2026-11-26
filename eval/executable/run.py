"""Run preregistered documents through the product CLI, using fresh model calls."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MODEL = "gpt-6.2-astra"
CASES = {
    "official": (None, "e7dae55e5453a250d93e335f5ff9c7f775f4457ccddd333f4a67e47b20d74381"),
    "hotel": (HERE / "card-capture.md", "1fa6bdbf13936a8dca643757c21c59ee59188eaf5190bd46a0ea5ab91f9b1798"),
    "aligned": (HERE / "card-capture-aligned.md", "8f1fd01653c66e1413c1b8e6b55a2dc5e1ca74dfa38a04b545d22845b89d13ef"),
    "ledger": (HERE / "public-ledger/post-pending.md", "d795152a745d3d1674d28ae5ebc7548015c92f4743a17435efbd8d8e9b29def0"),
    "fee": (HERE / "minor-units/merchant-fee.md", "9e9912ebc20ec9e54a8908be53e005a38cfdc8cec99310cf9a2850a5c38dda29"),
    "sdk-original": (HERE / "public-ledger/sdk-original.md", "9656e8a15c00c8a02baa5208463bc4ec039797471f934cc6d703a62a53f42cd9"),
    "sdk-fixed": (HERE / "public-ledger/sdk-fixed.md", "0b21941de1b1a761178e7b4510e321f47efa45cb7ad8061e8d37e8f3f1603198"),
}


def documents(official, names):
    result = {}
    for name in names:
        local, expected = CASES[name]
        path = (local or official).resolve()
        if sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"{name}: SHA-256 не совпал с зафиксированным входом")
        result[name] = path
    return result


def main():
    parser = argparse.ArgumentParser(description="Настоящие сквозные прогоны по протоколу исполняемых примеров")
    parser.add_argument("official", type=Path, help="Оригинальный requirements.md BCC")
    parser.add_argument("output", type=Path, help="Новый каталог результатов")
    parser.add_argument("--case", choices=CASES, action="append", dest="cases")
    parser.add_argument("--engine", type=Path, default=ROOT, help="Копия репозитория с проверяемым движком")
    parser.add_argument("--parallel", type=int, choices=range(1, 5), default=1)
    args = parser.parse_args()
    sources = documents(args.official, args.cases or CASES)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)

    def run(name):
        subprocess.run([sys.executable, "-m", "reviewer", sources[name], out / (name + ".json"),
                        "--model", MODEL, "--workers", "3"], cwd=args.engine.resolve(), check=True)

    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        list(pool.map(run, sources))


if __name__ == "__main__":
    main()
