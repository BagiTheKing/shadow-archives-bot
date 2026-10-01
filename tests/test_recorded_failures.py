"""
Regression tests: each one replays a failure a real run had (a recorded
reply or error) and checks the pipeline now handles it. Offline, no keys.

    python -m unittest discover tests
"""

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import longform_llm as L
import preflight


class Reply:
    def __init__(self, text):
        msg = types.SimpleNamespace(content=text)
        self.choices = [types.SimpleNamespace(message=msg, finish_reason="stop")]


class Client:
    def __init__(self, answers):
        self.answers = answers           # model -> text, or Exception
        self.asked = []
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, model, **kw):
        self.asked.append(model)
        a = self.answers[model]
        if isinstance(a, Exception):
            raise a
        return Reply(a)


class Pool:
    """Two keys of one provider and one other provider, like the real chain."""
    def __init__(self, answers, clients=None):
        self.models = list(answers)
        self._clients = clients or {m: Client(answers) for m in answers}

    def client_for(self, m):
        return self._clients[m]

    def provider_for(self, m):
        return m.split("/")[0]

    def api_name(self, m):
        return m          # the fake clients answer by the full name


def fresh():
    L._preferred_model = None
    L._retired.clear()
    L._exhausted_providers.clear()
    L._parse_failures.clear()
    L._junk_replies.clear()
    L._size_limits.clear()


class Gateway(unittest.TestCase):
    def setUp(self):
        fresh()

    def test_placeholder_packaging_goes_to_next_model(self):
        # 2026-10-01 trend video: titled "...", description "..." too
        pool = Pool({"groq/a": '{"title": "...", "description": "...", "tags": ["..."]}',
                     "cerebras/b": '{"title": "Rommel\'s Last Choice", "description": '
                                   '"How it ended.", "tags": ["rommel"]}'})
        got = L._call(pool, "s", "u", 500,
                      expect={"title": str, "description": str, "tags": list})
        self.assertEqual(got["title"], "Rommel's Last Choice")

    def test_request_too_large_routes_by_size(self):
        # 2026-10-01: the Gulf War article (116,000 characters) sent to groq
        big = "x" * 200_000
        pool = Pool({"groq/a": Exception("Error code: 413 - Request too large"),
                     "gemini/g": '{"topic": "Gulf War"}'})
        got = L._call(pool, "s", big, 500, expect={"topic": str})
        self.assertEqual(got["topic"], "Gulf War")
        self.assertNotIn("groq/a", pool.client_for("groq/a").asked)   # never sent

    def test_denied_key_retires_all_its_models(self):
        # 2026-09-30: the second Gemini key's project, 403 "denied access"
        denied = Exception("Error code: 403 - Your project has been denied access")
        shared = Client({"gemini/x@2": denied, "gemini/y@2": denied, "groq/a": '{"ok": "yes"}'})
        other = Client({"groq/a": '{"ok": "yes"}'})
        pool = Pool({"gemini/x@2": denied, "gemini/y@2": denied, "groq/a": '{"ok": "yes"}'},
                    clients={"gemini/x@2": shared, "gemini/y@2": shared, "groq/a": other})
        L._call(pool, "s", "u", 100, expect={"ok": str})
        self.assertIn("gemini/y@2", L._retired)

    def test_empty_chapter_rejected(self):
        self.assertTrue(L._chapter_reply_problem({"segments": []}))
        self.assertIsNone(L._chapter_reply_problem(
            {"segments": [{"narration": "He crossed the river at dawn."}]}))

    def test_empty_maps_rejected(self):
        # 2026-10-01 Rommel: the maps request came back empty
        self.assertTrue(L._reply_problem({"maps": []}, {"maps": list}))


class Checklist(unittest.TestCase):
    def script(self, **kw):
        s = {"topic": "Rommel's forced suicide (1944), Herrlingen",
             "title": "...", "description": "...\nA field marshal is given a choice.",
             "comment_question": "...", "intro_epigraph": {"text": "..."},
             "verdict_epigraph": None, "word_count": 1200,
             "chapters": [{"scenes": [{"narration": "a b c"}]}] * 4}
        s.update(kw)
        return s

    def test_placeholders_repaired(self):
        s = self.script()
        items = preflight.script_check(s)
        self.assertEqual(s["title"], "Rommel's forced suicide (1944)")
        self.assertEqual(s["description"], "A field marshal is given a choice.")
        self.assertEqual(s["comment_question"], "")
        self.assertTrue(preflight._real(preflight._quote_text(s["intro_epigraph"])))
        self.assertTrue(preflight._real(preflight._quote_text(s["verdict_epigraph"])))
        self.assertFalse(any(ok == "block" for ok, _, _ in items))

    def test_missing_map_reported(self):
        # 2026-10-01 Rommel: Germany and North Africa, no map
        s = self.script(title="How Hitler Killed His Favorite General")
        items = preflight.script_check(s, {"plan": {"places": ["North Africa", "Germany"]}})
        self.assertIn((False, "Maps"), [(ok, n) for ok, n, _ in items])

    def test_article_source_restored(self):
        # 2026-10-01 Rommel: "No sources appeared"
        s = self.script(title="T T T")
        preflight.script_check(s, {"sources": {"title": "Erwin Rommel",
                                               "url": "https://en.wikipedia.org/wiki/Erwin_Rommel"}})
        self.assertEqual(s["source_article"]["title"], "Erwin Rommel")

    def test_placeholder_description_blocks_upload(self):
        items = preflight.upload_check("Real Title", "Intro\n...\n0:00 Start", None)
        self.assertEqual(items[0][0], "block")


if __name__ == "__main__":
    unittest.main()
