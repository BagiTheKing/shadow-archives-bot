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


class FindStory(unittest.TestCase):
    """Topic scout, 2026-10-01: every event topic was dropped unresearched."""

    def setUp(self):
        import longform_main as M
        import longform_llm
        self.M = M
        self.saved = (M.resolve_topic, M.build_story, M.pick_topic,
                      longform_llm._anchor_fits_niche, longform_llm.idea_to_event)
        self.built = []
        M.resolve_topic = lambda lane, history=None, extra_avoid=(): (
            "The 1995 Qatari coup d'etat", False, False, 1995)
        M.pick_topic = lambda *a, **k: None
        M.build_story = lambda t, d, given=False: (self.built.append(t) or {"plan": {}})
        longform_llm._anchor_fits_niche = lambda t, story=False: "principle" not in t
        longform_llm.idea_to_event = lambda pool, idea, avoid: (
            "The Dreyfus affair (1894): the wrongful conviction and execution of Captain Dreyfus")

    def tearDown(self):
        import longform_llm
        (self.M.resolve_topic, self.M.build_story, self.M.pick_topic,
         longform_llm._anchor_fits_niche, longform_llm.idea_to_event) = self.saved

    def test_event_topic_is_researched(self):
        got = self.M.find_story("timely", None)
        self.assertEqual(got["topic"], "The 1995 Qatari coup d'etat")
        self.assertEqual(self.built, ["The 1995 Qatari coup d'etat"])

    def test_idea_becomes_its_event(self):
        self.M.resolve_topic = lambda lane, history=None, extra_avoid=(): (
            "Maimonides' principle", True, True, None)
        self.M.get_pool = lambda: None
        got = self.M.find_story("encore", None)
        self.assertEqual(got["topic"], "The Dreyfus affair (1894)")
        self.assertFalse(got["from_short"])

    def test_queued_topic_not_picked_again(self):
        got = self.M.find_story("timely", None, avoid={"The 1995 Qatari coup d'etat"})
        self.assertIsNone(got)


def _art(title, years, extra=""):
    paras = [f"In {y}, {extra} events in Gaza involving Hamas and Fatah continued." for y in years]
    return {"title": title, "url": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
            "text": "\n".join(paras) * 3}


class ArticleCheck(unittest.TestCase):
    """Topic scout, 2026-10-01: "Anti-Hamas insurgency in the Gaza Strip"
    (2023-) was taken for the 2007 Hamas takeover of Gaza."""
    TOPIC = ("The 2007 Hamas takeover of Gaza was a coup that displaced Fatah "
             "forces and established Hamas as the ruler")

    def test_later_article_refused(self):
        import story_mode as sm
        art = _art("Anti-Hamas insurgency in the Gaza Strip", [2007, 2023, 2024, 2024, 2025, 2025])
        self.assertIn("after the 2010 cutoff", sm.article_problem(self.TOPIC, art))

    def test_right_article_accepted(self):
        import story_mode as sm
        art = _art("Battle of Gaza (2007)", [2006, 2007, 2007, 2007, 2008])
        self.assertIsNone(sm.article_problem(self.TOPIC, art))

    def test_unrelated_article_refused(self):
        import story_mode as sm
        art = {"title": "Dreyfus affair", "url": "u",
               "text": "\n".join(f"In {y} the officer was tried in Paris." for y in
                                 [1894, 1895, 1898, 1899, 1906]) * 3}
        self.assertIsNotNone(sm.article_problem(self.TOPIC, art))

    def test_gather_skips_wrong_article_and_adds_related(self):
        import story_mode as sm
        wrong = _art("Anti-Hamas insurgency in the Gaza Strip", [2023, 2024, 2024, 2025, 2025])
        right = _art("Battle of Gaza (2007)", [2006, 2007, 2007, 2007, 2008], "fighting")
        leader = {"title": "Ismail Haniyeh", "url": "https://en.wikipedia.org/wiki/Ismail_Haniyeh",
                  "text": ("In 2006 Haniyeh became prime minister after the election won by Hamas. " * 8
                           + "\nIn 2024 he was killed in Tehran. " * 8)}
        saved = (sm.article_queries, sm.candidates, sm._fetch, sm._with_sources)
        try:
            sm.article_queries = lambda pool, topic, call: (["Hamas takeover of Gaza"], ["Ismail Haniyeh"])
            sm.candidates = lambda q: iter([dict(wrong), dict(right)])
            sm._fetch = lambda t: dict(leader) if t == "Ismail Haniyeh" else None
            sm._with_sources = lambda a: a.update(sources={"title": a["title"], "url": a["url"],
                                                           "sentences": []}) or a
            got = sm.gather(None, self.TOPIC, None)
        finally:
            sm.article_queries, sm.candidates, sm._fetch, sm._with_sources = saved
        self.assertEqual(got["title"], "Battle of Gaza (2007)")
        self.assertEqual([r["title"] for r in got["related"]], ["Ismail Haniyeh"])
        self.assertIn("prime minister", got["prompt_text"])
        self.assertNotIn("Tehran", got["prompt_text"])     # cut to the story's years


class Maps(unittest.TestCase):
    """2026-10-01 Rommel: no map although the story crossed North Africa,
    Normandy and Germany. "North Africa" has no coordinates on Wikipedia."""

    def setUp(self):
        import map_engine as ME
        self.ME = ME
        self.saved = (ME._wikipedia_coords, ME._wikipedia_search_coords, ME._load)
        ME._wikipedia_coords = lambda n: None        # worst case: Wikipedia down
        ME._wikipedia_search_coords = lambda n: None
        ME._load = lambda name: []                   # no Natural Earth either
        ME._GEOCODED.clear()

    def tearDown(self):
        self.ME._wikipedia_coords, self.ME._wikipedia_search_coords, self.ME._load = self.saved
        self.ME._GEOCODED.clear()

    def chapters(self):
        sc = lambda t: {"narration": t}
        return [{"scenes": [sc("Cold open.")]},
                {"scenes": [sc("Rommel's men drove east across North Africa toward Egypt.")]},
                {"scenes": [sc("Then came the summer in Normandy.")]},
                {"scenes": [sc("At home in Herrlingen, two generals arrived.")]},
                {"scenes": [sc("Verdict.")]}]

    def test_broad_region_is_placed(self):
        self.assertEqual(self.ME.geocode("North Africa"), (17.0, 28.0))
        self.assertEqual(self.ME.geocode("the Balkans"), (21.0, 43.0))

    def test_map_carries_its_coordinates(self):
        ch = self.chapters()
        L._place_map({"target": "North Africa", "origin": "", "action": "mark", "light": []},
                     ch, "Rommel", first=1, anywhere=True)
        got = [s["map"] for c in ch for s in c["scenes"] if s.get("map")]
        self.assertEqual(got[0]["ll"], [17.0, 28.0])

    def test_unplaceable_target_leaves_room_for_the_next(self):
        ch = self.chapters()
        L._place_map({"target": "Herrlingen", "origin": "", "action": "mark", "light": []},
                     ch, "Rommel", first=1, anywhere=True)      # nothing can place it
        self.assertFalse(any(s.get("map") for c in ch for s in c["scenes"]))
        L._place_map({"target": "North Africa", "origin": "", "action": "mark", "light": []},
                     ch, "Rommel", first=1, anywhere=True)
        self.assertTrue(any(s.get("map") for c in ch for s in c["scenes"]))

    def test_render_uses_saved_coordinates(self):
        calls = []
        self.ME.geocode = lambda n, _g=self.ME.geocode: calls.append(n) or _g(n)
        try:
            spec = self.ME.build_spec({"target": "Tobruk", "ll": [23.96, 32.08]}, [], 6.0)
        finally:
            self.ME.geocode = self.ME.geocode.__defaults__[0]
        self.assertEqual(spec["targets"][0]["ll"], (23.96, 32.08))
        self.assertEqual(calls, [])
