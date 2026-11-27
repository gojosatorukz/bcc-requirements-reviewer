"""Inspect one uploaded document using the same extraction and JSON checks as a full review."""

import json
import sys

from .input import INPUT_LIMITS, extract_document
from .json_examples import inspect_examples
from .segments import segment


def inspect_structure(raw, suffix):
    lines, metadata = extract_document(raw, suffix)
    if not any(line.strip() for line in lines):
        raise ValueError("Документ пуст.")
    blocks = segment(lines, metadata.get("pdf_json_blocks", ()))
    examples = inspect_examples(lines, blocks)["examples"]
    diagnostics = [{"line": item["line"], "message": item["diagnostic"]}
                   for item in examples if item["status"] != "valid"]
    return {"lines": len(lines), "units": sum(b["kind"] != "разметка" for b in blocks),
            "tables": sum(b["kind"] == "строка таблицы" for b in blocks),
            "examples": len(examples), "diagnostics": diagnostics}


def main():
    # Limit large binary parsers in their own process on the hosted Linux instance.
    if sys.platform == "linux":
        import resource
        for key, target in ((resource.RLIMIT_AS, 300 * 1024**2), (resource.RLIMIT_CPU, 20)):
            _, hard = resource.getrlimit(key)
            resource.setrlimit(key, (target if hard == resource.RLIM_INFINITY else min(target, hard), hard))
    suffix = sys.argv[1]
    try:
        result = inspect_structure(sys.stdin.buffer.read(INPUT_LIMITS[suffix] + 1), suffix)
    except UnicodeDecodeError:
        result = {"error": "Сохраните текстовый файл в UTF-8."}
    except ValueError as exc:
        result = {"error": str(exc)[:220]}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
