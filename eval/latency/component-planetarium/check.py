"""Verify two actual checkpoints for the interrupted planetarium run against its predeclared oracle."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from reviewer import response_fields
from reviewer.contract import validate_shape
from reviewer.full import inspect_examples, object_shapes
from reviewer.segments import segment

oracle = importlib.util.spec_from_file_location("planetarium_oracle", ROOT / "eval/field-obligations/replay.py")
evaluator = importlib.util.module_from_spec(oracle)
oracle.loader.exec_module(evaluator)
source = ROOT / "eval/field-obligations/planetarium.md"
assert sha256(source.read_bytes()).hexdigest() == evaluator.INPUTS["planetarium"]
lines = source.read_text().splitlines()
nonblank = {line: text for line, text in enumerate(lines, 1) if text.strip()}
blocks = segment(lines)
shapes = object_shapes(lines, blocks, inspect_examples(lines, blocks))
candidates = response_fields.candidates(response_fields.response_groups(nonblank, shapes, blocks))
assessments = []
for name, subset in (("fields-1-6.json", candidates[:6]), ("fields-7.json", candidates[6:])):
    env = json.loads((HERE / name).read_bytes())
    assert env["attempts"][-1]["status"] == "accepted" and not any(a.get("restored") for a in env["attempts"])
    validate_shape(env["result"], response_fields.SCHEMA, name)
    response_fields.verify(env["result"], subset, nonblank)
    assessments.extend(env["result"]["assessments"])
print(json.dumps(evaluator.obligations({"response_field_review": {
    "candidates": response_fields.describe(candidates), "assessments": assessments}}, False),
                 indent=2, ensure_ascii=False))
