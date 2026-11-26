"""Compare the observed outcome of a revised rule with a separately recorded choice."""

from .contract import EVIDENCE, TEXT, record
from .full import array, choice, citations


MATCH, MISMATCH, UNCONFIRMED = "выполнено", "противоречит решению", "не подтверждено"
OBSERVATION = record(id=TEXT, observed_side=choice("first", "second", "neither", "unclear"),
                     reason=TEXT, evidence=array(EVIDENCE))
RECORDED = record(side=choice("first", "second"), outcome=TEXT, scenario=TEXT, source=EVIDENCE,
                  observed_side=OBSERVATION["properties"]["observed_side"],
                  reason=TEXT, evidence=array(EVIDENCE), status=choice(MATCH, MISMATCH, UNCONFIRMED))

INSTRUCTIONS = """
Для каждого id из observe_witness выведи ровно одно observations. Примени новую редакцию
к исходному сценарию, сопоставь с first и second указанного свидетельства. В observed_side
выбери first или second только если новая редакция однозначно предписывает именно этот результат.
Если явно предписан третий результат или сценарий явно исключён, укажи neither. Если сохранилось
противоречие, не хватает сведений или по тексту возможны оба исхода, ставь unclear. Статус
самого дефекта оцени отдельно. При определённом исходе нужно устранение прежней неопределённости
и хотя бы одна точная цитата из изменённой строки связанного блока diff. Для unclear приведи
доступные основания и назови сохраняющуюся неопределённость. В reason укажи конкретное
наблюдаемое поведение. Не предполагай, что предпочитает владелец.
"""


def bind_choices(issues, specs, report_sha, expected_sha):
    if not specs:
        if expected_sha is not None:
            raise ValueError("SHA-256 базового отчёта указан без решения владельца")
        return {}
    if expected_sha != report_sha:
        raise ValueError("Для решения владельца нужен точный SHA-256 исходного JSON-отчёта")
    choices = {}
    by_id = {issue["finding"]["id"]: issue for issue in issues}
    for spec in specs:
        fid, _, side = spec.partition("=")
        if side not in {"first", "second"} or fid in choices or fid not in by_id:
            raise ValueError(f"Ожидался один выбор first или second для проверяемого замечания: {spec}")
        issue = by_id[fid]
        finding = issue["finding"]
        witnesses = (finding["triage"]["witness"] if "triage" in finding else issue["candidates"])
        if finding["priority"] != "критический" or finding["category"] != "нарушение" or len(witnesses) != 1:
            raise ValueError(f"{fid}: для решения нужна одна подтверждённая пара исходов")
        choices[fid] = (side, witnesses[0])
    return choices


def verdict(side, observed):
    if observed == "unclear":
        return UNCONFIRMED
    return MATCH if side == observed else MISMATCH


def verify_observations(observations, choices, checks, new_lines, diff):
    if sorted(row["id"] for row in observations) != sorted(choices):
        raise ValueError("Нужно одно наблюдение для каждого выбранного решения")
    by_id = {row["id"]: row for row in checks}
    after = {hunk["id"]: {entry["line"] for entry in hunk["after"]} for hunk in diff}
    for row in observations:
        check = by_id[row["id"]]
        if row["evidence"] or row["observed_side"] != "unclear":
            citations(row["evidence"], new_lines)
        if row["observed_side"] == "unclear":
            continue
        cited = {entry["line"] for entry in row["evidence"]}
        if check["status"] != "устранено" or not any(cited & after[h] for h in check["diff_hunks"]):
            raise ValueError(f"{row['id']}: определённый исход нужно подтвердить цитатой из исправления устранённого дефекта")


def attach_observations(checks, observations, choices):
    by_id = {row["id"]: row for row in checks}
    for observation in observations:
        side, witness = choices[observation["id"]]
        observed = observation["observed_side"]
        by_id[observation["id"]]["owner_decision"] = {
            "side": side, "scenario": witness["scenario"], "outcome": witness[side]["outcome"],
            "source": witness[side]["source"], "observed_side": observed, "status": verdict(side, observed),
            "reason": observation["reason"], "evidence": observation["evidence"],
        }
