"""Compare a simple two-way cutoff with adjacent, explicitly tabulated examples."""

import re


# Both clauses must state complementary comparisons at exactly the same integer.
NUMBER = r"(?<![\w.,−-])(?P<n>\d{1,9})(?![\w.,]\d|\d)"
UNIT = r"(?:\s+[\w-]+){0,2}"
CUTOFFS = (
    ("le", (rf"(?:не более|не больше|at most|<=|≤)\s*{NUMBER}",
            rf"{NUMBER}{UNIT}\s+(?:(?:или|и) (?:меньше|менее)|or (?:fewer|less))\b",
            rf"до\s+{NUMBER}\s+включительно")),
    ("ge", (rf"(?:не менее|не меньше|at least|>=|≥)\s*{NUMBER}",
            rf"{NUMBER}{UNIT}\s+(?:(?:или|и) (?:больше|более)|or more)\b")),
    ("gt", (rf"(?<!не )(?:больше|более|свыше|выше|more than|above|>(?!=))\s*{NUMBER}",)),
    ("lt", (rf"(?<!не )(?:меньше|менее|ниже|less than|below|<(?!=))\s*{NUMBER}",)),
)
PATTERNS = [(op, re.compile(expr, re.I)) for op, exprs in CUTOFFS for expr in exprs]
EXAMPLE = re.compile(r"при[её]мк|пример|контрол|acceptance|examples?", re.I)
EXCEPTION = re.compile(r"\b(?:кроме|исключ\w*|иначе|либо|только|unless|except|otherwise|not|never|no)\b"
                       r"|\bне\s+(?!(?:более|больше|менее|меньше)\b)|\bи\s+если\b|\band\s+if\b", re.I)


def comparison(clause, labels):
    if EXCEPTION.search(clause) or len(re.findall(r"\d+", clause)) != 1:
        return None
    matches = {(op, int(m["n"])) for op, pattern in PATTERNS for m in pattern.finditer(clause)}
    named = [label for label in labels if re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", clause, re.I)]
    if len(matches) == 1 and len(named) == 1:
        op, number = matches.pop()
        return op, number, named[0]
    return None


def boundary_questions(lines, blocks, split_cells):
    """Only compare a nearby paired rule with a two-column table and both adjacent controls."""
    for i, header in enumerate(blocks):
        if header["kind"] != "шапка таблицы":
            continue
        columns = split_cells(lines[header["start"] - 1])
        if len(columns) != 2:
            continue
        rows = []
        for row_index in range(i + 1, len(blocks)):
            block = blocks[row_index]
            if block["kind"] == "разметка":
                continue
            if block.get("header") != header["start"]:
                break
            values = split_cells(lines[block["start"] - 1])
            if len(values) != 2:
                break
            rows.append((values, block["start"]))
        if len(rows) < 3:
            continue
        numeric = [j for j in (0, 1) if all(re.fullmatch(r"\d{1,9}", v[j]) for v, _ in rows)]
        if len(numeric) != 1:
            continue
        axis = numeric[0]
        observed = {int(v[axis]): (v[1-axis], n) for v, n in rows}
        labels = {value for value, _ in observed.values()}
        if (len(observed) != len(rows) or len(labels) != 2 or
                any(not re.fullmatch(r"[\w][\w -]{0,55}", label) for label in labels)):
            continue

        # The two immediately preceding sentences can straddle a heading introducing examples.
        clauses = []
        for block in reversed(blocks[max(0, i - 4):i]):
            if block["kind"] == "заголовок" and not clauses and EXAMPLE.search(lines[block["start"] - 1]):
                continue
            if block["kind"] != "текст":
                break
            n = block["start"]
            clauses = [(part.strip(), n) for part in re.split(r"[.!?;]+(?:\s+|$)|\s+а\s+(?=если\b)", lines[n - 1],
                                                              flags=re.I) if part.strip()] + clauses
            if len(clauses) >= 2:
                break
        if len(clauses) < 2:
            continue
        pair = [comparison(clause, labels) for clause, _ in clauses[-2:]]
        if not all(pair):
            continue
        (op1, n, label1), (op2, other_n, label2) = pair
        rule = {op1: label1, op2: label2}
        if n != other_n or label1 == label2 or not {n-1, n, n+1}.issubset(observed):
            continue
        if set(rule) == {"le", "gt"}:
            low, high, at_limit = rule["le"], rule["gt"], rule["le"]
        elif set(rule) == {"lt", "ge"}:
            low, high, at_limit = rule["lt"], rule["ge"], rule["ge"]
        else:
            continue
        actual, row = observed[n]
        if (observed[n-1][0], observed[n+1][0]) != (low, high) or actual == at_limit:
            continue
        references = sorted({clauses[-2][1], clauses[-1][1], header["start"], row})
        yield ("table_cutoff", f"Уточните результат на границе {n}",
               f"При «{columns[axis]}» = {n} какой результат нужен в столбце «{columns[1-axis]}»: "
               f"«{at_limit}» по формулировке правила или «{actual}» по таблице?", references)
