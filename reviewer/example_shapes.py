"""Describe the observed top-level keys in each valid JSON object."""

import json

from .json_examples import example_body


def object_shapes(lines, segments, checks):
    by_start = {block["start"]: block for block in segments}
    shapes = []
    for example in checks["examples"]:
        if example["status"] != "valid":
            continue
        start, end = example["start"], example["end"]
        _, body = example_body(lines, start, end)
        # Shape extraction does not need numeric values, including very long integers.
        value = json.loads(body, parse_int=str, parse_float=str)
        if isinstance(value, dict):
            keys = list(value)
            shapes.append({"start": start, "end": end, "section": by_start[start]["section"],
                           "keys": keys[:32], "key_count": len(keys)})
    return shapes
