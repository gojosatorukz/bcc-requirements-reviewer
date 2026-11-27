"""Build importable Jira tasks from the open findings of a validated full report."""

import csv
from io import StringIO
import re

from .executable import unexecuted_message
from .execution_view import acceptance_lines, first_step_comparisons


PRIORITIES = {"критический": "Highest", "существенный": "Medium", "редакционный": "Low"}


def csv_cell(text):
    # CSV quoting protects column boundaries. A spreadsheet may still evaluate the cell.
    if re.match(r"^[\s\ufeff\x00-\x1f]*[=+@\-＝＋＠－]", text) or text.startswith(("\t", "\r", "\n")):
        return "'" + text
    return text


def description(finding, run, pages, execution):
    lines = [f"{finding['id']} · {finding['category']} · {finding['priority']}",
             "Вопрос владельцу. " + finding["question"], "Адресат. " + finding["owner"],
             "Основание. " + finding["reason"], "Следующее действие. " + finding["action"]]
    for case in finding.get("triage", {}).get("witness", []):
        lines.extend(["", "Сценарий для QA. " + case["scenario"]])
        for side, letter in (("first", "А"), ("second", "Б")):
            reading = case[side]
            lines.append(f"Правило {letter}, строка {reading['source']['line']}. {reading['outcome']}")
    if execution and execution["status"] == "executed":
        fields = {f["key"]: f for f in execution["program"]["states"]}
        trace = execution["result"]["scenario"]
        lines.append(f"Расчёт. Первое расхождение на шаге {trace['first_difference']['step']}.")
        for key, first, second in first_step_comparisons(trace):
            field = fields[key]
            unit = "" if isinstance(first, str) or field["unit"] == "1" else " " + field["unit"]
            control = execution["result"]["control"]["final"]["first"][key]
            lines.append(f"{field['label']} · А = {first}{unit}, Б = {second}{unit}. "
                         f"В контрольном примере обе трактовки дают {control}{unit} в конце.")
    elif execution and execution.get("acceptance_example"):
        lines.extend(acceptance_lines(execution))
    elif execution:
        title, reason = unexecuted_message(execution)
        lines.append(f"{title}. {reason}")
    lines.extend(["", f"Источник. {run['input_name']}", f"SHA-256 документа. {run['input_sha256']}"])
    for ref in finding["evidence"]:
        n = ref["line"]
        page = next((p["page"] for p in pages if p["start"] <= n <= p["end"]), None)
        location = f"Строка {n}" + (f", страница {page}" if page else "")
        lines.append(f"{location} · {ref['quote']}")
    return "\n".join(lines)


def issues_csv(report):
    stream = StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(("Summary", "Issue Type", "Priority", "Description"))
    executions = {e["finding_id"]: e for e in report.get("executables", {}).get("items", [])}
    run = report["run"]
    for finding in report["whole"]["findings"]:
        title = " ".join(finding["title"].split())
        # Reserve space for both the source finding ID and the optional spreadsheet guard.
        suffix = f" [{finding['id']}]"
        summary = csv_cell(title[:254 - len(suffix)] + suffix)
        body = description(finding, run, run.get("pages", []), executions.get(finding["id"]))
        writer.writerow((summary, "Task", PRIORITIES[finding["priority"]], csv_cell(body)))
    return stream.getvalue().encode("utf-8-sig")
