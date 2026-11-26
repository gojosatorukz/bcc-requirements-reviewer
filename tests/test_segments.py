import unittest

from reviewer.segments import segment


class SegmentationTest(unittest.TestCase):
    def test_table_and_multiline_example_keep_context(self):
        lines = ["# API", "", "Code | Meaning", "--- | ---", "201 | Created",
                 "#### 201", "~~~json", "{", "", '  "ok": true', "}", "~~~", "After"]
        blocks = segment(lines)
        row = next(b for b in blocks if b["kind"] == "строка таблицы")
        self.assertEqual((row["start"], row["header"]), (5, 3))
        example = next(b for b in blocks if b["kind"] == "пример json")
        self.assertEqual((example["start"], example["end"], example["section"]), (7, 12, "API / 201"))
        covered = [n for b in blocks for n in range(b["start"], b["end"] + 1) if lines[n-1].strip()]
        self.assertEqual(covered, [n for n, line in enumerate(lines, 1) if line.strip()])
