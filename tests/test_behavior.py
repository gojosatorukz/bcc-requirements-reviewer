import unittest

from reviewer import behavior
from reviewer.segments import segment


class BehaviorTests(unittest.TestCase):
    def read(self, lines):
        source = dict(enumerate(lines, 1))
        payload, anchors = behavior.reading(segment(lines), source)
        return source, payload, anchors

    def test_focused_pass_keeps_authored_table_headings_and_numbered_sources(self):
        lines = ["# Notes", "## Functional requirements", "A can be archived.",
                 "## Acceptance tests", "| Given | When | Expected result |", "| --- | --- | --- |",
                 "| A | Archive | A stays active |", "## Changelog", "Changed the font."]
        _, payload, _ = self.read(lines)
        self.assertEqual(payload['rules'], [3])
        self.assertEqual(payload['examples'], [7])
        self.assertEqual(set(payload['source']), {3, 5, 7})

    def test_unknown_headings_and_long_rows_preserve_exact_original_quotes(self):
        source, payload, anchors = self.read(["# Книга", "Часть правила. " * 45, "Итог изменён."])
        rule, other = payload["rules"][0], payload["rules"][-1]
        candidate = {"title": "Регистрация", "scenario": "У записи меняют статус", "owner": "Редактор",
                     "question": "Какой статус нужен?", "agreement_control": "Без смены статус прежний.",
                     "first": {"anchor":rule,"outcome":"Старая запись"}, "second":{"anchor":other,"outcome":"Новая запись"}}
        item, = behavior.expand({"conflicts":[candidate]}, anchors, source)["conflicts"]
        self.assertEqual((item["first"]["source"]["line"], item["second"]["source"]["line"]), (2, 3))
        self.assertIn(item["first"]["source"]["quote"], source[2])
        with self.assertRaisesRegex(ValueError, "anchor"):
            behavior.expand({"conflicts":[dict(candidate, first={"anchor":-1,"outcome":"Старая запись"})]}, anchors, source)
        self.assertEqual(behavior.expand({"conflicts":[]}, anchors, source), {"conflicts":[]})
