"""Independently replay the two pinned Go README examples on TigerBeetle 0.17.2."""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
import time

from .run import HERE, documents


VERSION = "TigerBeetle version 0.17.2+c9b910a"
BALANCES = ("debits_pending", "debits_posted", "credits_pending", "credits_posted")


@contextmanager
def replica(binary):
    with TemporaryDirectory(prefix="bcc-tigerbeetle-") as tmp:
        ledger = Path(tmp) / "sdk.tigerbeetle"
        log = Path(tmp) / "replica.log"
        subprocess.run([binary, "format", "--cluster=0", "--replica=0", "--replica-count=1",
                        "--development", ledger], capture_output=True, check=True, timeout=40)
        with log.open("w") as stderr:
            server = subprocess.Popen([binary, "start", "--addresses=127.0.0.1:0",
                                       "--development", ledger], stdout=stderr, stderr=stderr)
            try:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline and server.poll() is None:
                    address = re.search(r"listening on (127\.0\.0\.1:\d+)", log.read_text())
                    if address:
                        yield address[1]
                        return
                    time.sleep(0.1)
                raise RuntimeError(f"Replica did not start: {log.read_text()}")
            finally:
                server.terminate()
                server.wait(timeout=10)


def repl(binary, address, command):
    result = subprocess.run([binary, "repl", "--cluster=0", f"--addresses={address}",
                             f"--command={command};"], text=True, capture_output=True, check=True, timeout=20)
    remaining = result.stdout.strip()
    records = []
    while remaining:
        record, end = json.JSONDecoder().raw_decode(remaining)
        records.append(record)
        remaining = remaining[end:].lstrip()
    return records


def check(binary, source):
    # Both SHA-checked Go snippets have identical accounts, transfer IDs and amounts.
    # Only the first transfer's flags change. Translate that field literally.
    field = source.read_text().splitlines()[65].split("Flags:", 1)[1].strip(" ,")
    suffix = {"0": "", "TransferFlags{Pending: true}.ToUint16()": " flags=pending"}[field]
    with replica(binary) as address:
        created = repl(binary, address, "create_accounts id=101 code=1 ledger=1, id=102 code=1 ledger=1")
        if [item["status"].split(".")[-1] for item in created] != ["created", "created"]:
            raise ValueError("Test accounts were not created")
        queries = [
            f"create_transfers id=6 debit_account_id=101 credit_account_id=102 amount=10 ledger=1 code=1{suffix}",
            f"create_transfers id=7 amount={2 ** 128 - 1} pending_id=6 flags=post_pending_transfer",
        ]
        events = []
        for query in queries:
            result, = repl(binary, address, query)
            accounts = repl(binary, address, "lookup_accounts id=101, id=102")
            balances = {row["id"]: {key: int(row[key]) for key in BALANCES} for row in accounts}
            events.append({"request": query, "status": result["status"].split(".")[-1], "balances": balances})
    return {"source_flag": field, "events": events}


def verify(cases):
    for name, case in cases.items():
        first, second = case["events"]
        fixed = name == "sdk-fixed"
        if first["status"] != "created" or second["status"] != ("created" if fixed else "pending_transfer_not_pending"):
            raise ValueError(f"{name}: unexpected status")
        for account, side in (("101", "debits"), ("102", "credits")):
            opposite = "credits" if side == "debits" else "debits"
            for event, pending, posted in [(first, 10 if fixed else 0, 0 if fixed else 10), (second, 0, 10)]:
                expected = {side + "_pending": pending, side + "_posted": posted,
                            opposite + "_pending": 0, opposite + "_posted": 0}
                if event["balances"][account] != expected:
                    raise ValueError(f"{name}/{account}: expected {expected}, got {event['balances'][account]}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path, help="Official TigerBeetle release 0.17.2 binary")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    binary = args.binary.resolve()
    version = subprocess.run([binary, "version"], text=True, capture_output=True, check=True).stdout.strip()
    if version != VERSION:
        raise ValueError(f"Expected {VERSION}, got {version}")
    sources = documents(HERE, ("sdk-original", "sdk-fixed"))
    result = {name: check(binary, source) for name, source in sources.items()}
    verify(result)
    text = json.dumps({"engine": version, "cases": result}, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
