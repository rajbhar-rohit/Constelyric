"""Tests for the pure text helpers.  Run:  python -m unittest tests.test_text"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lyric_text as T
import symbols as S


class CodeStyle(unittest.TestCase):
    def test_every_form(self):
        seen = {}
        for i in range(60):                                            # find lines of each kind
            words = [(w, 0, 1) for w in ("got", "that", f"gas{i}")]
            seg, kind = T.code_segments(words)
            seen.setdefault(kind, "".join(s for s, _ in seg))
        self.assertEqual(set(seen), {"dot", "comment", "call"})
        self.assertTrue(seen["dot"].count(".") == 2)
        self.assertTrue(seen["comment"].startswith("// got that"))
        self.assertTrue(seen["call"].startswith("got(that, ") and seen["call"].endswith(");"))

    def test_deterministic_and_single_words(self):
        w = [("Midnight,", 0, 1), ("drive", 1, 2)]
        self.assertEqual(T.code_segments(w), T.code_segments(w))
        self.assertEqual(T.code_segments([("Hey!", 0, 1)]), ([("hey", "word")], "dot"))

    def test_pronouns_are_marked(self):
        seg, kind = T.code_segments([("I", 0, 1), ("got", 1, 2), ("that", 2, 3)])
        if kind == "dot":
            self.assertEqual(seg[0], ("i", "pron"))

    def test_apostrophes_and_punctuation(self):
        self.assertEqual(T.code_tokens(["I'm", "(done)", "—", "Don’t"]), ["im", "done", "dont"])

    def test_typing_follows_the_words(self):
        w = [("midnight", 1.0, 2.0), ("drive", 2.0, 3.0)]
        self.assertEqual(T.typed_text(w, 0.5), "")
        self.assertEqual(T.typed_text(w, 1.0), "m")                                     # first letter right away
        self.assertEqual(T.typed_text(w, 1.4), "midni")
        self.assertEqual(T.typed_text(w, 1.9), "midnight")
        self.assertTrue(T.typed_text(w, 2.5).startswith("midnight dr"))
        self.assertEqual(T.typed_text(w, 9), "midnight drive")


class Icons(unittest.TestCase):
    def test_lookup_across_languages(self):
        self.assertEqual(S.symbol_for("left"), "←")
        self.assertEqual(S.symbol_for("Feel"), "♥")
        self.assertEqual(S.symbol_for("love"), "♥")
        self.assertEqual(S.symbol_for("dil"), "♥")
        self.assertEqual(S.symbol_for("आँखें"), "👁")
        self.assertEqual(S.symbol_for("chand"), "☾")
        self.assertEqual(S.symbol_for("you"), "👤")
        self.assertEqual(S.symbol_for("stars"), "★")

    def test_no_false_friends(self):
        for w in ("mon", "die", "kar", "or", "the", "pain", "ask"):
            self.assertIsNone(S.symbol_for(w), w)


if __name__ == "__main__":
    unittest.main()
