from unittest import TestCase

from reviewer import source_refs
from reviewer.full import MODEL_WHOLE, RANKED_WHOLE
from reviewer.contract import EVIDENCE


class SourceRefTests(TestCase):
    def test_expands_only_evidence_and_preserves_the_precise_clause_for_a_witness(self):
        lines = {1: "first clause and second clause", 2: "ok"}
        refs, extra = source_refs.references(lines)
        witness = {"source": {"line": 1, "quote": "second clause"}}
        document = {"evidence": [1, 2], "witness": witness}
        self.assertEqual(source_refs.expand(document, refs),
                         {"evidence": [{"line": 1, "quote": lines[1]}, {"line": 2, "quote": "ok"}],
                          "witness": witness})
        self.assertEqual(extra, {})
        self.assertEqual(document["evidence"], [1, 2])
        props = source_refs.wire_schema(MODEL_WHOLE)["properties"]["findings"]["items"]["properties"]
        self.assertEqual(props["evidence"]["items"], {"type": "integer"})
        self.assertEqual(RANKED_WHOLE["properties"]["findings"]["items"]["properties"]["evidence"]["items"], EVIDENCE)

    def test_unknown_anchors_are_rejected_and_long_lines_are_bounded_verbatim(self):
        source = {4: "long word " * 110, 9: "ok"}
        refs, fragments = source_refs.references(source)
        self.assertTrue(all(n > 9 and cell["line"] == 4 for n, cell in fragments.items()))
        self.assertEqual(" ".join(cell["quote"] for cell in fragments.values()), source[4].strip())
        self.assertTrue(all(len(cell["quote"]) <= 300 for cell in fragments.values()))
        for n in (3, 4, True, "9"):
            with self.subTest(n=n), self.assertRaises(ValueError):
                source_refs.expand({"evidence": n}, refs)
