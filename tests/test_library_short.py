"""Library entries and the checks of the model's today half (offline)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import library_short as L

HEAD = "Russia and Ukraine open peace talks in Abu Dhabi as Zelensky seeks security guarantees"


def good():
    return {"fit": True,
            "line1": "This week, Russia and Ukraine opened peace talks in Abu Dhabi, and Ukraine wants security guarantees.",
            "line2": "But in 1994, Ukraine was promised security once before.",
            "now": "Now Ukraine wants a guarantee that holds.",
            "question": "Would you trust a promise like that again?", "end_title": "TRUST IT AGAIN?",
            "now_tag": "ABU DHABI · 2026", "now_person": 0,
            "title": "Ukraine Wants Security Guarantees. In 1994 It Gave Up Nukes for One #shorts",
            "description": "x", "tags": ["a"]}


class T(unittest.TestCase):
    def setUp(self):
        self.entries = L.load()
        self.by = {e["slug"]: e for e in self.entries}

    def test_entries_valid(self):
        self.assertGreaterEqual(len(self.entries), 5)
        for e in self.entries:
            self.assertEqual(L.validate(e), [], e["slug"])

    def test_matching(self):
        c = L.candidates([HEAD, "Cat show"], self.entries)
        self.assertEqual(c[0][1]["slug"], "budapest-1994")
        self.assertEqual(L.candidates(["Cat show"], self.entries), [])
        self.assertNotIn("budapest-1994", [e["slug"] for _, e in L.candidates([HEAD], self.entries, recent={"budapest-1994"})])
        self.assertEqual(L.match_score("Ukrainian forces advance", self.by["budapest-1994"]), 1)
        self.assertEqual(L.match_score("Sukraine", self.by["budapest-1994"]), 0)

    def test_good_passes(self):
        self.assertEqual(L.today_problems(good(), HEAD, self.by["budapest-1994"]), [])

    def test_rejects(self):
        e = self.by["budapest-1994"]
        for k, v in (("line1", "Ukraine opened talks."),
                     ("line1", "This week, Russia and Ukraine opened talks in Geneva, Switzerland."),
                     ("line1", "This week, 40 delegates opened peace talks in Abu Dhabi."),
                     ("line1", "This week, two delegations opened peace talks in Abu Dhabi."),
                     ("line2", "Long ago it was promised security."),
                     ("question", "Would you trust it"), ("end_title", "trust it again?"),
                     ("title", "Ukraine wants guarantees"), ("now_person", 9)):
            s = good(); s[k] = v
            self.assertTrue(L.today_problems(s, HEAD, e), (k, v))
        s = good(); s["now"] = " ".join(["word"] * 40)
        self.assertTrue(L.today_problems(s, HEAD, e))

    def test_build(self):
        e = self.by["budapest-1994"]
        short, src, meta = L.build(e, good(), "credit", "http://x/y.jpg")
        shows = [l["show"] for l in short["lines"]]
        self.assertEqual(shows[:2], ["opening", "opening"])
        self.assertEqual(shows[-2:], ["now", "end"])
        self.assertEqual(src["now"]["url"], "http://x/y.jpg")
        self.assertTrue(meta["title"].endswith("#shorts"))


if __name__ == "__main__":
    unittest.main()
