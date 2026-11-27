"""Run paired fresh proposals on the same confirmed witness, with one shared auditor."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from reviewer.__main__ import engine_digest, run_usage, version
from reviewer.executable import VERSION, build_one, targets
from reviewer.full_render import validate_report

SAMPLES = [
    ("month", "runs/bcc-executable/review.json", "D-01"),
    ("push", "runs/bcc-executable/review.json", "D-02"),
    ("hotel", "eval/executable/runs/hotel.json", "D-01"),
    ("fee", "eval/executable/runs/fee.json", "D-01"),
    ("status", "eval/executable/runs/before-named-state.json", "D-01"),
    ("sdk", "eval/executable/runs/before-sdk-original.json", "D-02"),
]
ASTRA, SOL = "gpt-6.2-astra", "gpt-6.2-sol"


def run_pair(sample, output, order, commit, digest):
    tag, filename, fid = sample
    raw = (ROOT / filename).read_bytes()
    review = json.loads(raw)
    lines = validate_report(review)
    witness = targets(review, VERSION)[fid]
    finding = next(f for f in review["whole"]["findings"] if f["id"] == fid)

    def trial(proposer):
        path = output / (tag + "-" + proposer.rsplit("-", 1)[1] + ".json")
        if path.exists():
            raise ValueError(f"Result from this round already exists: {path.name}")
        start = monotonic()
        result, attempts = build_one(fid, witness, lines, ASTRA, None, digest,
                                     finding=finding, proposal_model=proposer)
        row = {"document": tag, "baseline": filename, "baseline_sha256": sha256(raw).hexdigest(),
               "finding_id": fid, "commit": commit, "engine_sha256": digest, "proposer": proposer,
               "auditor": ASTRA, "utc_end": datetime.now(timezone.utc).isoformat(),
               "seconds": round(monotonic() - start, 2), **run_usage(attempts), "attempts": attempts, "item": result}
        path.write_text(json.dumps(row, ensure_ascii=False, indent=2) + "\n")
        print(tag, proposer, result["status"], row["seconds"], flush=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(trial, order))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--reverse", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    commit = version()
    digest = engine_digest("full.py", "executable.py", "witness_vm.py")
    samples = list(reversed(SAMPLES)) if args.reverse else SAMPLES
    for i, sample in enumerate(samples):
        order = (ASTRA, SOL) if (i + int(args.reverse)) % 2 == 0 else (SOL, ASTRA)
        run_pair(sample, args.out, order, commit, digest)


if __name__ == "__main__":
    main()
