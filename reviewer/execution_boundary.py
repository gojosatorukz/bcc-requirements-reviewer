"""Find the first disagreement along a one-dimensional integer path in a validated report."""

from copy import deepcopy

from .witness_vm import simulate


MAX_REPLAYS_PER_EXAMPLE = 32
MAX_REPLAYS_PER_REPORT = 96


def _coordinates(case):
    groups = [case["initial"], *(event["values"] for event in case["events"])]
    return [{row["key"]: row["value"] for row in rows} for rows in groups]


def _single_axis(program):
    control, scenario = program["control"], program["scenario"]

    def event_series(case):
        return [(e["kind"], e["label"]) for e in case["events"]]

    if event_series(control) != event_series(scenario):
        return None
    axis = None
    for step, (left, right) in enumerate(zip(_coordinates(control), _coordinates(scenario))):
        if list(left) != list(right):
            return None
        for key, start in left.items():
            end = right[key]
            if start == end:
                continue
            if axis or type(start) is not int or type(end) is not int:
                return None
            axis = {"step": step, "key": key, "start": start, "end": end}
    return axis


def scan_boundary(item, lines, budget=MAX_REPLAYS_PER_EXAMPLE):
    """Return an optional panel and the number of additional VM calls actually spent."""
    if item["status"] != "executed":
        return None, 0
    program = item["program"]
    axis = _single_axis(program)
    if not axis:
        return None, 0
    start, end = axis["start"], axis["end"]
    direction = 1 if start < end else -1
    case = deepcopy(program["control"])
    rows = case["events"][axis["step"] - 1]["values"] if axis["step"] else case["initial"]
    edited = next(row for row in rows if row["key"] == axis["key"])
    previous = item["result"]["control"]
    used = 0
    for value in range(start + direction, end + direction, direction):
        if value == end:
            # Both endpoints were already recomputed from this program by validate_report.
            trace = item["result"]["scenario"]
        else:
            if used >= min(budget, MAX_REPLAYS_PER_EXAMPLE):
                return dict(axis, status="limited", last=value - direction), used
            used += 1
            edited["value"] = value
            try:
                trace = simulate(program, lines, case)
            except ValueError:
                # Skipping an undefined input would turn a later hit into a false first hit.
                return dict(axis, status="undefined", failed=value), used
        diff = trace["first_difference"]
        if diff:
            same = previous["steps"][diff["step"] - 1]
            key = diff["field"]
            return dict(axis, status="found", equal_input=value - direction, different_input=value,
                        output=key, output_step=diff["step"], equal_values=[same["first"][key], same["second"][key]],
                        different_values=[diff["first"], diff["second"]]), used
        previous = trace
    return None, used
