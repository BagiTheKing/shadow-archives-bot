"""
Tests for the visual-quality upgrade: archival sourcing, shot pacing, and the
on-screen typography fixes. No network — everything here is pure logic or
in-memory Pillow.
"""

import base64
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
import unittest.mock
import inspect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PIL import Image                                       # noqa: E402

import archive_engine                                       # noqa: E402
import audio_engine                                         # noqa: E402
import caption_track                                        # noqa: E402
import formats                                              # noqa: E402
import llm_engine                                           # noqa: E402
import llm_provider                                         # noqa: E402
import longform_llm                                         # noqa: E402
import longform_main                                        # noqa: E402
import longform_video                                       # noqa: E402
import longform_visual                                      # noqa: E402
import main                                                 # noqa: E402
import thumbnail_engine                                     # noqa: E402
import video_engine                                         # noqa: E402
import topics                                               # noqa: E402
import visual_engine                                        # noqa: E402
import youtube_publisher                                    # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import reconcile_playlist                                   # noqa: E402



class _FakeRequest:
    """The googleapiclient calling convention: build a request, execute it."""

    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


def _info(width=1600, height=1000, mime="image/jpeg"):
    return {"width": width, "height": height, "mime": mime}


class TestCommonsFiltering(unittest.TestCase):
    """Commons returns SOMETHING for almost any query; the filter decides
    whether the video shows a photograph or a coat of arms."""

    def test_good_landscape_photo_scores(self):
        self.assertIsNotNone(
            archive_engine._candidate_score(_info(), "File:HMS Hood 1937.jpg"))

    def test_junk_titles_rejected(self):
        for title in ("File:Flag of Ukraine.png", "File:Coat of arms of Kiev.png",
                      "File:Ukraine locator map.png", "File:Navy insignia.jpg"):
            self.assertIsNone(archive_engine._candidate_score(_info(), title),
                              title)

    def test_tiny_files_rejected(self):
        self.assertIsNone(
            archive_engine._candidate_score(_info(width=640, height=480),
                                            "File:HMS Hood.jpg"))

    def test_extreme_aspect_rejected(self):
        self.assertIsNone(
            archive_engine._candidate_score(_info(width=4000, height=900),
                                            "File:Panorama.jpg"))

    def test_landscape_outranks_portrait(self):
        landscape = archive_engine._candidate_score(
            _info(1600, 1000), "File:A.jpg")
        portrait = archive_engine._candidate_score(
            _info(1000, 1600), "File:B.jpg")
        self.assertGreater(landscape, portrait)

    def test_strip_html(self):
        self.assertEqual(
            archive_engine._strip_html('<a href="/wiki/x">Yoichi Okamoto</a>'),
            "Yoichi Okamoto")


class TestFitFrame(unittest.TestCase):

    def test_near_landscape_is_cropped_to_exact_size(self):
        img = Image.new("RGB", (1600, 1000), (200, 30, 30))
        out = archive_engine._fit_frame(img, 1920, 1080)
        self.assertEqual(out.size, (1920, 1080))

    def test_portrait_is_padded_not_amputated(self):
        img = Image.new("RGB", (800, 1400), (200, 200, 200))
        out = archive_engine._fit_frame(img, 1920, 1080)
        self.assertEqual(out.size, (1920, 1080))
        # The blur-pad path darkens the edges; a pure crop would leave the
        # corner as bright as the centre.
        self.assertLess(sum(out.getpixel((5, 5))),
                        sum(out.getpixel((960, 540))))


class TestShotPacing(unittest.TestCase):
    """No still may outlive MAX_SHOT_SECONDS — the '13 images in 9 minutes'
    fix lives in these two helpers."""

    def test_short_scene_is_one_shot(self):
        self.assertEqual(longform_visual._shots_for(8.0), 1)

    def test_long_scene_is_split(self):
        self.assertEqual(longform_visual._shots_for(20.0), 2)
        self.assertEqual(longform_visual._shots_for(30.0), 3)

    def test_split_is_capped(self):
        self.assertEqual(longform_visual._shots_for(90.0),
                         longform_visual.MAX_SHOTS_PER_SCENE)

    def test_shot_durations_reassemble_to_scene_length(self):
        for n in (1, 2, 3):
            durs = longform_video._shot_durations(24.0, n)
            self.assertEqual(len(durs), n)
            total = sum(durs) - longform_video.INNER_XFADE * (n - 1)
            self.assertAlmostEqual(total, 24.0, places=6)


class TestOnScreenTypography(unittest.TestCase):

    def test_stat_is_a_single_event_with_forced_break(self):
        """The '1,300' colliding with its own label was two independent events
        at fixed margins. One event + \\N cannot collide with itself."""
        event = longform_video._stat_event(10.0, "1,300", "warheads given up")
        self.assertTrue(event.startswith("Dialogue: 1,"))
        self.assertIn(r"\N", event)
        self.assertIn(r"\q2", event)          # the value may never wrap
        self.assertIn("WARHEADS GIVEN UP", event)
        self.assertNotIn("\n", event)         # literally one ASS event

    def test_stat_without_label_has_no_break(self):
        self.assertNotIn(r"\N", longform_video._stat_event(10.0, "72", ""))

    def test_stat_strips_ass_braces(self):
        event = longform_video._stat_event(1.0, "{9}", "{x}")
        self.assertNotIn("{9}", event)

    def test_chapter_title_event_shape(self):
        event = longform_video._chapter_title_event(0.3, 12.0, 2, "The Decision")
        self.assertIn("PART TWO", event)
        self.assertIn("THE DECISION", event)
        self.assertIn(r"\move", event)        # it animates in

    def test_chapter_title_never_outlives_its_scene(self):
        event = longform_video._chapter_title_event(0.0, 2.6, 2, "The Chain")
        # start 0:00:00.00, end must be within the 2.6s scene
        self.assertIn("0:00:02.", event)


class TestGenerationResolution(unittest.TestCase):
    """Asking flux for 2 MP when it was trained on 1 MP is what produced the
    duplicated hands and fingers. Generate at native, upscale after."""

    def test_oversized_request_is_scaled_to_about_one_megapixel(self):
        w, h = visual_engine._generation_size(1920, 1080)
        self.assertLess(w * h, 1_200_000)
        self.assertGreater(w * h, 800_000)

    def test_aspect_ratio_is_preserved(self):
        w, h = visual_engine._generation_size(1920, 1080)
        self.assertAlmostEqual(w / h, 1920 / 1080, places=1)
        pw, ph = visual_engine._generation_size(1080, 1920)
        self.assertAlmostEqual(pw / ph, 1080 / 1920, places=1)

    def test_sizes_are_multiples_of_sixteen(self):
        for size in (visual_engine._generation_size(1920, 1080)
                     + visual_engine._generation_size(1080, 1920)):
            self.assertEqual(size % 16, 0)

    def test_already_small_requests_are_left_alone(self):
        self.assertEqual(visual_engine._generation_size(768, 768), (768, 768))


class TestPaintedStyle(unittest.TestCase):
    """The painterly suffix hides what free models get wrong; photorealism
    exposes it. Both pipelines must keep asking for paint."""

    def test_longform_suffix_is_painterly(self):
        self.assertIn("oil painting", longform_llm.STYLE_SUFFIX)
        self.assertNotIn("photorealistic", longform_llm.STYLE_SUFFIX)

    def test_shorts_prompt_requests_the_same_style(self):
        self.assertIn("oil painting", llm_engine.SYSTEM_PROMPT)
        self.assertNotIn("35mm", llm_engine.SYSTEM_PROMPT)


class TestStyleIsEnforcedNotRequested(unittest.TestCase):
    """The style used to be an instruction to the script model. A model that
    wrote its own ('photorealistic, 8k') kept it and had ours appended after,
    so the generator received two styles and the photoreal half won."""

    def test_competing_style_words_are_stripped(self):
        out = visual_engine.build_generation_prompt(
            "wide shot of a bank lobby, photorealistic, 8k, shot on 35mm film")
        for banned in ("photorealistic", "8k", "35mm"):
            self.assertNotIn(banned, out.lower())
        self.assertIn("oil painting", out)

    def test_subject_survives_intact(self):
        out = visual_engine.build_generation_prompt(
            "low-angle, 15th-century carrack under torchlight, photograph")
        self.assertIn("15th-century carrack under torchlight", out)

    def test_style_applied_exactly_once(self):
        once = visual_engine.build_generation_prompt("a ship")
        twice = visual_engine.build_generation_prompt(once)
        self.assertEqual(twice.count("oil painting"), 1)
        self.assertEqual(once, twice)

    def test_empty_prompt_still_yields_something_renderable(self):
        self.assertIn("oil painting", visual_engine.build_generation_prompt(""))

    def test_flux_is_tried_before_any_other_model(self):
        """turbo renders in a different look, so it must only ever rescue a
        frame flux could not produce at all."""
        import inspect
        src = inspect.getsource(visual_engine.download_image)
        self.assertLess(src.index("model=flux"), src.index("model=turbo"))


class TestEraIsStampedOnEveryFrame(unittest.TestCase):
    """Telling the script model 'this is 1928' is a request it can forget by
    the ninth image prompt. The year is stamped on at generation instead."""

    def test_era_clause_names_year_and_decade(self):
        out = visual_engine.build_generation_prompt("a bank lobby", era_year=1928)
        self.assertIn("set in the year 1928", out)
        self.assertIn("1920s", out)
        self.assertIn("nothing modern", out)

    def test_era_clause_is_not_stacked_on_renormalisation(self):
        once = visual_engine.build_generation_prompt("a lobby", 1928)
        twice = visual_engine.build_generation_prompt(once, 1928)
        self.assertEqual(twice.count("set in the year"), 1)
        self.assertEqual(once, twice)

    def test_no_era_leaves_prompt_undated(self):
        self.assertNotIn("set in the year",
                         visual_engine.build_generation_prompt("a ship"))

    def test_era_read_from_a_topic_line(self):
        self.assertEqual(visual_engine.era_from_text(
            "Operation Eagle Claw (1980), the Iran hostage rescue"), 1980)
        self.assertIsNone(visual_engine.era_from_text("no year here"))

    def test_longform_and_shorts_share_one_style_definition(self):
        self.assertEqual(longform_llm.STYLE_SUFFIX, visual_engine.CANONICAL_STYLE)


class TestRepetitionGate(unittest.TestCase):
    """Built against a real published script. Chapter 4 of the Zeebrugge video
    was almost entirely these sentences on a loop, and every existing check
    passed it: valid JSON, enough segments, enough words."""

    PUBLISHED_PADDING = (
        "The plan failed. The German fleet had the open canal. The port stayed "
        "open. The dead could not be recovered. The archive holds the wreck. "
        "The plan failed. The German fleet had the open canal. The port stayed "
        "open. The dead could not be recovered. The decision was a mistake. "
        "The story ends with the wreck. The plan failed. The German fleet had "
        "the open canal. The port stayed open. The dead could not be recovered."
    )

    REAL_WRITING = (
        "Thetis hit the net defence, lost steering and grounded early. "
        "Intrepid and Iphigenia followed but drifted off course in the smoke. "
        "After-action reports cited inadequate channel marking. "
        "Buoys had shifted by tidal currents. "
        "Motor launches failed to illuminate the critical turn. "
        "Three weeks later a second raid used Vindictive as a block ship. "
        "She grounded five hundred yards short of the lock gates."
    )

    def _chapter(self, text):
        return {"segments": [{"narration": text, "image_prompt": "wide shot"}]}

    def test_the_published_padding_is_now_rejected(self):
        reason = longform_llm._chapter_repetition(
            self._chapter(self.PUBLISHED_PADDING))
        self.assertIsNotNone(reason)
        self.assertIn("repeating itself", reason)

    def test_real_writing_passes(self):
        self.assertIsNone(
            longform_llm._chapter_repetition(self._chapter(self.REAL_WRITING)))

    def test_a_chapter_echoing_an_earlier_one_is_rejected(self):
        earlier = longform_llm._sentences(self.REAL_WRITING)
        reason = longform_llm._chapter_repetition(
            self._chapter(self.REAL_WRITING), previous_sentences=earlier)
        self.assertIsNotNone(reason)
        self.assertIn("earlier chapters", reason)

    def test_short_chapters_are_not_judged(self):
        self.assertIsNone(longform_llm._chapter_repetition(
            self._chapter("A single line. And one more.")))

    def test_sentence_normalisation_ignores_case_and_punctuation(self):
        a = longform_llm._sentences("The plan failed.")
        b = longform_llm._sentences("the plan failed")
        self.assertEqual(a, b)

    def test_validate_chapter_rejects_padding(self):
        self.assertFalse(longform_llm._validate_chapter(
            self._chapter(self.PUBLISHED_PADDING), expected_words=20))

    def test_prompt_no_longer_ships_a_copyable_casualty_figure(self):
        """'1,415' was an example value and a model published it as this
        story's death toll."""
        prompt = longform_llm._chapter_prompt(
            "Some disaster", "The Cost", "brief", 200, "")
        self.assertNotIn("1,415", prompt)
        self.assertNotIn("Hood", prompt)

    def test_prompt_hands_over_what_was_already_said(self):
        prompt = longform_llm._chapter_prompt(
            "Some disaster", "The Chain", "brief", 200, "",
            covered=["The port stayed open."])
        self.assertIn("ALREADY BEEN NARRATED", prompt)
        self.assertIn("The port stayed open.", prompt)


class TestWriterProvider(unittest.TestCase):

    KEYS = ("GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY",
            "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY")

    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in self.KEYS}

    def tearDown(self):
        for key, value in self._saved.items():
            os.environ.pop(key, None)
            if value:
                os.environ[key] = value

    def test_falls_back_to_openrouter_without_a_gemini_key(self):
        os.environ["OPENROUTER_API_KEY"] = "test-key"
        self.assertFalse(llm_provider.gemini_available())
        self.assertIn("openrouter", llm_provider.describe_provider().lower())

    def test_a_run_with_no_keys_at_all_says_so(self):
        """It used to claim OpenRouter whatever was configured, so a run with
        no keys reported a writer it did not have."""
        self.assertEqual(llm_provider.describe_provider(), "none configured")

    def test_the_free_tiers_come_before_the_one_that_bills(self):
        for key in ("GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY",
                    "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
            os.environ[key] = "test-key"
        order = llm_provider.configured_providers()
        self.assertEqual(order[-1], "anthropic")
        self.assertEqual(order[0], "gemini")

    def test_every_provider_is_optional(self):
        os.environ["GROQ_API_KEY"] = "test-key"
        self.assertEqual(llm_provider.configured_providers(), ["groq"])

    def test_gemini_detected_when_configured(self):
        os.environ["GEMINI_API_KEY"] = "test-key"
        self.assertTrue(llm_provider.gemini_available())

    def test_model_names_are_discovered_not_hardcoded(self):
        """Two hand-written Gemini IDs went stale and returned 404 'no longer
        available to new users', killing a run. The list comes from the API."""
        class FakeModel:
            def __init__(self, i): self.id = i

        class FakeClient:
            class models:
                @staticmethod
                def list():
                    return [FakeModel("models/gemini-2.0-flash"),
                            FakeModel("models/gemini-flash-latest"),
                            FakeModel("models/text-embedding-004"),
                            FakeModel("models/imagen-3.0"),
                            FakeModel("models/gemini-2.5-pro")]

        found = llm_provider.discover_gemini_models(FakeClient())
        self.assertNotIn("text-embedding-004", found)
        self.assertNotIn("imagen-3.0", found)
        self.assertTrue(all("gemini" in m for m in found))
        self.assertNotIn("/", found[0])          # the 'models/' prefix is gone
        self.assertIn("flash", found[0])         # flash outranks pro

    def test_discovery_failure_falls_back_to_generic_aliases(self):
        class Broken:
            class models:
                @staticmethod
                def list():
                    raise RuntimeError("network down")

        self.assertEqual(llm_provider.discover_gemini_models(Broken()),
                         llm_provider.GEMINI_FALLBACK_MODELS)

    def test_pool_chains_gemini_then_openrouter(self):
        """Gemini answering 404 and 429 must not end a run when an OpenRouter
        key is sitting right there — which is exactly what happened."""
        pool = llm_provider.ModelPool([
            ("gemini-flash-latest", "gclient", "gemini"),
            ("some/model:free", "orclient", "openrouter"),
        ])
        self.assertEqual(pool.models[0], "gemini-flash-latest")
        self.assertEqual(pool.client_for("some/model:free"), "orclient")
        self.assertIn("gemini -> openrouter", pool.describe())

    def test_empty_pool_is_falsey(self):
        self.assertFalse(llm_provider.ModelPool([]))


class TestUnspeakableNarration(unittest.TestCase):
    """A punctuation-only segment reached gTTS, which asserts rather than
    raising a normal error, and killed a run 27 minutes in with a finished
    script already on disk."""

    def test_real_narration_is_speakable(self):
        self.assertTrue(audio_engine.is_speakable("Eight helicopters took off."))
        self.assertTrue(audio_engine.is_speakable("114 died"))

    def test_punctuation_and_blanks_are_not(self):
        for junk in ("", "   ", "—", "...", "-- , .", "\n\t", None):
            self.assertFalse(audio_engine.is_speakable(junk), repr(junk))

    def test_non_latin_narration_still_counts_as_speakable(self):
        self.assertTrue(audio_engine.is_speakable("Прип'ять"))

    def test_chapter_with_an_unsayable_segment_is_rejected(self):
        chapter = {"segments": [
            {"narration": "A real sentence.", "image_prompt": "wide shot"},
            {"narration": " — ", "image_prompt": "close-up"},
        ]}
        self.assertFalse(longform_llm._chapter_usable(chapter))

    def test_ordinary_chapter_still_passes(self):
        chapter = {"segments": [
            {"narration": "A real sentence.", "image_prompt": "wide shot"},
        ]}
        self.assertTrue(longform_llm._chapter_usable(chapter))


class TestCloudflareProvider(unittest.TestCase):
    """Workers AI is the quality upgrade, but it must stay strictly optional:
    with no credentials the pipeline has to behave exactly as it did before."""

    def setUp(self):
        for k in ("CF_ACCOUNT_ID", "CF_API_TOKEN",
                  "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"):
            os.environ.pop(k, None)

    tearDown = setUp

    def test_disabled_without_credentials(self):
        self.assertFalse(visual_engine.cloudflare_available())
        self.assertIsNone(visual_engine.cloudflare_generate("x", 1024, 576))

    def test_partial_credentials_do_not_enable_it(self):
        os.environ["CF_ACCOUNT_ID"] = "acct"
        self.assertFalse(visual_engine.cloudflare_available())

    def test_enabled_with_both_halves(self):
        os.environ["CF_ACCOUNT_ID"] = "acct"
        os.environ["CF_API_TOKEN"] = "tok"
        self.assertTrue(visual_engine.cloudflare_available())

    def test_legacy_variable_names_also_work(self):
        os.environ["CLOUDFLARE_ACCOUNT_ID"] = "acct"
        os.environ["CLOUDFLARE_API_TOKEN"] = "tok"
        self.assertTrue(visual_engine.cloudflare_available())

    def test_decodes_base64_json_replies(self):
        raw = io.BytesIO()
        Image.new("RGB", (8, 8), (10, 20, 30)).save(raw, "PNG")
        encoded = base64.b64encode(raw.getvalue()).decode()

        class Reply:
            headers = {"content-type": "application/json"}
            def json(self):
                return {"success": True, "result": {"image": encoded}}

        self.assertEqual(visual_engine._decode_cf_response(Reply()),
                         raw.getvalue())

    def test_decodes_raw_image_replies(self):
        class Reply:
            headers = {"content-type": "image/png"}
            content = b"\x89PNG-bytes"

        self.assertEqual(visual_engine._decode_cf_response(Reply()),
                         b"\x89PNG-bytes")

    def test_reports_an_unsuccessful_reply(self):
        class Reply:
            headers = {"content-type": "application/json"}
            def json(self):
                return {"success": False, "errors": [{"message": "quota"}]}

        with self.assertRaises(ValueError):
            visual_engine._decode_cf_response(Reply())

    def test_square_output_is_cropped_not_stretched(self):
        """flux-1-schnell may ignore the requested ratio. Stretching a square
        to 16:9 distorts every face in it, so it must be cropped."""
        square = Image.new("RGB", (1024, 1024))
        out = visual_engine._fit_cover(square, 1920, 1080)
        self.assertEqual(out.size, (1920, 1080))


class TestEraGates(unittest.TestCase):
    """The 1415-story-with-2020s-footage failure: every visual source is now
    gated on which side of the photography line the story sits."""

    def test_era_extracted_from_topic(self):
        self.assertEqual(longform_llm._era_from_topic(
            "In 1415 the Portuguese captured Ceuta."), 1415)
        self.assertEqual(longform_llm._era_from_topic(
            "The 1994 Budapest Memorandum, signed in 1994 after 1991."), 1991)
        self.assertIsNone(longform_llm._era_from_topic("A story with no year"))

    def test_pre_photography_prompt_asks_for_artwork(self):
        rule = longform_llm._archive_query_rule(1415)
        self.assertIn("painting", rule)
        self.assertIn("predates photography", rule)

    def test_post_photography_prompt_asks_for_photographs(self):
        rule = longform_llm._archive_query_rule(1941)
        self.assertIn("PHOTOGRAPHABLE", rule)

    def test_modern_photo_rejected_for_medieval_story(self):
        modern = {"title": "Gibraltar aerial view 2019.jpg"}
        self.assertFalse(longform_visual._era_appropriate(modern, 1415))

    def test_period_artwork_accepted_even_with_modern_year_in_title(self):
        art = {"title": "Conquest of Ceuta painting (1907 azulejo).jpg"}
        self.assertTrue(longform_visual._era_appropriate(art, 1415))

    def test_anything_goes_for_photographic_eras(self):
        modern = {"title": "Kyiv skyline 2001.jpg"}
        self.assertTrue(longform_visual._era_appropriate(modern, 1994))

    def test_broll_only_for_timeless_subjects(self):
        sea = {"archive_query": "Portuguese fleet 1415", "image_prompt": ""}
        self.assertEqual(longform_visual._timeless_broll_query(sea),
                         "dark rough sea waves")
        soldiers = {"archive_query": None,
                    "image_prompt": "low-angle, soldiers marching in formation"}
        self.assertIsNone(longform_visual._timeless_broll_query(soldiers))


class TestNoRepeatedFootage(unittest.TestCase):
    """A live run reused one stock clip four times and one photo twice."""

    def test_broll_query_is_not_reused(self):
        """A scene whose only timeless subject is already on screen gets no
        clip at all, rather than the same one again."""
        scene = {"archive_query": "surveyor's map", "image_prompt": ""}
        first = longform_visual._timeless_broll_query(scene, used=set())
        self.assertEqual(first, "antique map parchment")
        self.assertIsNone(
            longform_visual._timeless_broll_query(scene, used={first}))

    def test_scene_with_a_second_subject_falls_through_to_it(self):
        """'treaty' and 'map' both qualify: once the map clip is spent, the
        scene may still take the document clip instead of going without."""
        scene = {"archive_query": "old treaty map", "image_prompt": ""}
        self.assertEqual(
            longform_visual._timeless_broll_query(
                scene, used={"antique map parchment"}),
            "old handwritten letter candlelight")

    def test_a_second_subject_still_gets_its_own_clip(self):
        used = {"antique map parchment"}
        storm = {"archive_query": "", "image_prompt": "wide shot of a storm"}
        self.assertEqual(
            longform_visual._timeless_broll_query(storm, used),
            "dark storm clouds timelapse")


class TestGenerationOutageFallback(unittest.TestCase):
    """Thirty consecutive HTTP 500s turned a video into black screens with
    subtitles. A real frame already on disk is always the better stand-in."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.source = os.path.join(self.dir, "real.jpg")
        img = Image.new("RGB", (longform_visual.KB_W, longform_visual.KB_H))
        for x in range(0, img.width, 64):        # gradient, so crops differ
            for y in range(0, img.height, 64):
                img.paste((x % 256, y % 256, 120), (x, y, x + 64, y + 64))
        img.save(self.source, quality=90)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_reuse_produces_a_full_size_frame(self):
        out = os.path.join(self.dir, "out.jpg")
        self.assertTrue(longform_visual._reuse_real_frame([self.source], out, 0))
        self.assertEqual(Image.open(out).size,
                         (longform_visual.KB_W, longform_visual.KB_H))

    def test_reuse_is_not_the_same_framing_twice(self):
        a = os.path.join(self.dir, "a.jpg")
        b = os.path.join(self.dir, "b.jpg")
        longform_visual._reuse_real_frame([self.source], a, 0)
        longform_visual._reuse_real_frame([self.source], b, 1)
        self.assertNotEqual(open(a, "rb").read(), open(b, "rb").read())

    def test_reuse_declines_when_nothing_real_exists_yet(self):
        out = os.path.join(self.dir, "out.jpg")
        self.assertFalse(longform_visual._reuse_real_frame([], out, 0))


class TestPoliteArchiveRequests(unittest.TestCase):
    """Wikimedia answered a whole run with 'does not comply with our robot
    policy'. The client must identify itself and serialise its requests."""

    def test_user_agent_names_the_project_and_a_contact_url(self):
        ua = archive_engine.HEADERS["User-Agent"]
        self.assertIn("http", ua)
        self.assertIn("ArchiveParadoxBot", ua)

    def test_requests_are_spaced_and_back_off(self):
        self.assertGreater(archive_engine.DOWNLOAD_GAP_SECONDS, 0)
        self.assertTrue(archive_engine.RATE_LIMIT_BACKOFF)

    def test_thumbnails_are_not_requested_larger_than_needed(self):
        import inspect
        self.assertIn("\"iiurlwidth\": 1600",
                      inspect.getsource(archive_engine.search_commons))

    def test_credit_names_the_archive_it_came_from(self):
        loc = {"title": "Desert One wreckage", "license": "Library of Congress",
               "source": "Library of Congress"}
        self.assertIn("via Library of Congress", archive_engine.format_credit(loc))
        commons = {"title": "C-130", "license": "Public domain"}
        self.assertIn("via Wikimedia Commons",
                      archive_engine.format_credit(commons))


class TestGenerationVariants(unittest.TestCase):

    def test_every_attempt_uses_a_different_parameter_set(self):
        import inspect
        source = inspect.getsource(visual_engine.download_image)
        self.assertIn("variants[", source)
        self.assertIn("model=turbo", source)

    def test_generator_identifies_itself(self):
        self.assertIn("http", visual_engine.GEN_HEADERS["User-Agent"])


class TestArchiveQueryCleaning(unittest.TestCase):

    def test_normal_query_passes(self):
        self.assertEqual(longform_llm._clean_archive_query("HMS Hood 1941"),
                         "HMS Hood 1941")

    def test_null_and_junk_rejected(self):
        for bad in (None, 12, "", "a", "null", "N/A",
                    "a very long query with far too many words to be useful"):
            self.assertIsNone(longform_llm._clean_archive_query(bad), bad)

    def test_whitespace_and_quotes_normalised(self):
        self.assertEqual(
            longform_llm._clean_archive_query('  "Chernobyl   reactor 4" '),
            "Chernobyl reactor 4")


class TestEncoreTopicMatching(unittest.TestCase):
    """The encore lane picks the topic from the channel's own numbers, so a
    published video has to map back to a pool entry reliably."""

    def setUp(self):
        import channel_insights
        self.ci = channel_insights
        self.topic = self.ci.ALL_TOPICS[0]

    def test_exact_match_via_description_beats_guessing(self):
        """main.py writes the topic verbatim into every description. That path
        existed but was never given a description to read."""
        matched = self.ci.match_topic("A totally unrelated title",
                                      f"Some blurb\n\n{self.topic}\n\nSubscribe.")
        self.assertEqual(matched, self.topic)

    def test_fetched_videos_carry_the_description(self):
        import inspect
        src = inspect.getsource(self.ci.fetch_recent_videos)
        self.assertIn('"description"', src)

    def test_best_performing_topic_passes_the_description_through(self):
        import inspect
        src = inspect.getsource(self.ci.best_performing_topic)
        self.assertIn('v.get("description"', src)

    def test_unmatchable_video_returns_none_rather_than_a_wrong_topic(self):
        self.assertIsNone(self.ci.match_topic("zzz qqq", ""))


class TestYouTubeScopes(unittest.TestCase):

    def test_all_three_scopes_are_declared(self):
        import youtube_publisher as yp
        joined = " ".join(yp.SCOPES)
        for needed in ("youtube.upload", "youtube.readonly", "youtube.force-ssl"):
            self.assertIn(needed, joined)

    def test_helper_requests_the_same_scopes(self):
        import importlib.util, os as _os
        path = _os.path.join(_os.path.dirname(__file__), "..", "tools",
                             "get_youtube_refresh_token.py")
        spec = importlib.util.spec_from_file_location("tok", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        import youtube_publisher as yp
        self.assertEqual(set(mod.SCOPES), set(yp.SCOPES))


class TestCloudflarePayload(unittest.TestCase):
    """Workers AI rejected every request for as long as this integration
    existed, because flux-1-schnell's schema has no width/height:

        AiError: Bad input: Error: Additional or unevaluated properties
        '/width, /height' at '/' not allowed

    Every frame silently fell back to Pollinations, so the configured
    upgrade never rendered anything. These tests assert the wire payload,
    which is the only thing that would have caught it.
    """

    def setUp(self):
        os.environ["CF_ACCOUNT_ID"] = "acct"
        os.environ["CF_API_TOKEN"] = "tok"
        self._real_post = visual_engine.requests.post
        self.sent = {}

        def fake_post(url, headers=None, json=None, timeout=None):
            self.sent.clear()
            self.sent.update(json or {})
            raise RuntimeError("stop here, the payload is what matters")

        visual_engine.requests.post = fake_post

    def tearDown(self):
        visual_engine.requests.post = self._real_post
        for k in ("CF_ACCOUNT_ID", "CF_API_TOKEN"):
            os.environ.pop(k, None)

    def test_flux_payload_omits_dimensions(self):
        visual_engine.cloudflare_generate("a ship", 1080, 1920)
        self.assertIn("prompt", self.sent)
        self.assertIn("steps", self.sent)
        self.assertNotIn("width", self.sent)
        self.assertNotIn("height", self.sent)

    def test_sdxl_style_models_do_get_dimensions(self):
        original = visual_engine.CF_MODEL
        visual_engine.CF_MODEL = "@cf/bytedance/stable-diffusion-xl-lightning"
        try:
            visual_engine.cloudflare_generate("a ship", 1080, 1920)
            self.assertEqual(self.sent.get("width"), 1080)
            self.assertEqual(self.sent.get("height"), 1920)
        finally:
            visual_engine.CF_MODEL = original

    def test_model_capability_detection(self):
        self.assertFalse(visual_engine._cf_accepts_dimensions(
            "@cf/black-forest-labs/flux-1-schnell"))
        self.assertTrue(visual_engine._cf_accepts_dimensions(
            "@cf/bytedance/stable-diffusion-xl-lightning"))
        self.assertTrue(visual_engine._cf_accepts_dimensions(
            "@cf/lykon/dreamshaper-8-lcm"))

    def test_a_failure_returns_none_so_pollinations_takes_over(self):
        self.assertIsNone(visual_engine.cloudflare_generate("x", 1080, 1920))


# ---------------------------------------------------------------------------
# Published-history memory.
#
# The channel published three duplicate pairs, each a couple of hours apart:
# Light Brigade, Ariane 5 and the Great Fire of London. Every one of them was a
# manual trigger landing on the same date-based topic as that day's scheduled
# run. These tests are written against that exact history.
# ---------------------------------------------------------------------------

import datetime                                             # noqa: E402

import published_history as ph                              # noqa: E402
import topics                                               # noqa: E402


def _video(title, hours_ago, duration="PT52S", description="", likes=0, views=0):
    when = (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(hours=hours_ago))
    return {"videoId": title[:8], "title": title, "description": description,
            "publishedAt": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "likes": likes, "views": views, "duration": duration}


class TestTopicExclusion(unittest.TestCase):

    def test_an_excluded_topic_is_skipped(self):
        first = topics.pick_topic()
        second = topics.pick_topic(exclude=[first])
        self.assertNotEqual(first, second)
        self.assertIn(second, topics.CORE_TOPICS + topics.EXPERIMENT_TOPICS)

    def test_exhausted_pool_returns_none_rather_than_a_repeat(self):
        everything = topics.CORE_TOPICS + topics.EXPERIMENT_TOPICS
        self.assertIsNone(topics.pick_topic(exclude=everything))

    def test_same_day_reruns_used_to_collide(self):
        """The bug, stated as a test: without exclusion, two runs on one day get
        the same topic. That is what produced the duplicate pairs."""
        self.assertEqual(topics.pick_topic(), topics.pick_topic())

    def test_published_topics_never_come_back_round(self):
        """Every topic the channel has already covered is excluded at once, not
        one per run."""
        used = set(topics.CORE_TOPICS[:5])
        for offset in range(10):
            picked = topics.pick_topic(offset, exclude=used)
            self.assertNotIn(picked, used)


class TestDuplicateGuard(unittest.TestCase):

    def test_the_real_ariane_pair_would_have_been_blocked(self):
        """2026-08-07: 'The software error that destroyed the Ariane 5 rocket'
        at 14:02, then 'The Reused Code That Destroyed Ariane 5 Flight 501' at
        16:25. The second run is the one this guard stops."""
        history = [_video("The software error that destroyed the Ariane 5 rocket", 2.4)]
        self.assertTrue(ph.published_within(history))

    def test_yesterdays_upload_does_not_block_today(self):
        self.assertFalse(ph.published_within([_video("Yesterday's Short", 25)]))

    def test_window_is_ordered_newest_first(self):
        history = [_video("older", 8), _video("newest", 1), _video("middle", 4)]
        self.assertEqual([v["title"] for v in ph.published_within(history)],
                         ["newest", "middle", "older"])

    def test_undated_videos_are_not_treated_as_recent(self):
        self.assertFalse(ph.published_within([{"title": "x", "publishedAt": ""}]))


class TestCoveredTopics(unittest.TestCase):

    def test_the_exact_topic_line_in_a_description_is_matched(self):
        topic = topics.CORE_TOPICS[0]
        history = [_video("Any title at all", 100, description=f"blurb\n\n{topic}\n")]
        self.assertIn(topic, ph.covered_topics(history))

    def test_a_reworded_title_still_maps_back_to_its_topic(self):
        """No description to read — this is the path for everything published
        before main.py started writing the topic line."""
        history = [_video("The Reused Code That Destroyed Ariane 5 Flight 501", 100)]
        covered = ph.covered_topics(history)
        self.assertTrue(any("Ariane 5" in t for t in covered), covered)

    def test_longform_only_ignores_shorts(self):
        title = "The Mayor's Dismissal That Burned London"
        history = [_video(title, 100), _video(title, 90, duration="PT7M50S")]
        self.assertEqual(len(ph.covered_topics(history, longform_only=True)), 1)
        self.assertTrue(ph.covered_topics(history))


class TestMatcherOnRealTitles(unittest.TestCase):
    """Real titles from the channel, mapped back to real pool entries. Every one
    of these decides whether a topic gets excluded from the rotation, so a wrong
    answer here is a duplicate upload later."""

    CASES = [
        ("The Design Flaw That Destroyed HMS Hood", "HMS Hood"),
        ("The Cheap Repair That Destroyed the Sultana", "Sultana"),
        ("The Neglect That Killed 1,000 on the General Slocum", "General Slocum"),
        ("The Reused Code That Destroyed Ariane 5 Flight 501", "Ariane 5"),
        ("The Design Change That Killed 114 at Hyatt Regency", "Hyatt Regency"),
        ("The Safety Test That Destroyed Chernobyl Reactor Four", "Chernobyl"),
        ("The Launch Decision That Destroyed Challenger", "Challenger"),
        ("The Mayor's Dismissal That Burned London", "Great Fire of London"),
        ("The Math Error That Starved Air Canada Flight 143", "Gimli Glider"),
        ("Zeebrugge Raid 1918: Why the Blockships Failed", "Zeebrugge"),
        ("The Antikythera Mechanism: Why the First Computer Sank", "Antikythera"),
    ]

    def test_every_real_title_maps_to_its_own_story(self):
        import channel_insights
        for title, expected in self.CASES:
            matched = channel_insights.match_topic(title) or ""
            self.assertIn(expected, matched, f"{title!r} -> {matched[:60]!r}")

    def test_the_title_formula_alone_never_matches(self):
        """'design', 'destroyed', 'killed' and spoken numbers appear in nearly
        every title on the channel and in nearly every topic. On their own they
        are not evidence — this is what sent HMS Hood to the Tacoma Narrows."""
        import channel_insights
        self.assertIsNone(
            channel_insights.match_topic("The Design Error That Destroyed Six"))


class TestReachGate(unittest.TestCase):
    """The hit-rate problem: 7 of 42 Shorts cleared 500 views and the rest died
    between 5 and 150, on an identical pipeline. What separates them is whether
    the SUBJECT had an audience before the script was written."""

    def test_every_pool_topic_carries_a_rating(self):
        for topic in topics.CORE_TOPICS + topics.EXPERIMENT_TOPICS:
            self.assertIn(topics.REACH.get(topic), topics.REACH_ORDER, topic[:60])

    def test_unrated_topics_are_treated_as_narrow(self):
        self.assertEqual(topics.reach_of("something never rated"), topics.NARROW)

    def test_the_default_floor_only_picks_wide(self):
        published = set()
        for day in range(20):
            picked = topics.pick_topic(day, exclude=published)
            self.assertEqual(topics.reach_of(picked), topics.WIDE, picked[:60])
            published.add(picked)

    def test_measured_flops_are_rated_below_the_floor(self):
        """Not guesses — the channel published these and they died, and every
        one of them had been live long enough for that to mean something:
        Chernobyl 38 views in 15 days, Voynich 12 in 16, Zeebrugge 65 in 18."""
        for fragment, expected in (("Zeebrugge Raid (1918)", topics.NARROW),
                                   ("Gimli Glider", topics.NARROW),
                                   ("Chernobyl safety test", topics.SATURATED)):
            topic = next(t for t in topics.CORE_TOPICS if fragment in t)
            self.assertEqual(topics.reach_of(topic), expected, fragment)

    def test_a_day_old_upload_never_rates_its_topic(self):
        """The Great Fire of London was rated SATURATED on two uploads that were
        24 hours old. A day-old Short has few views because it is a day old.
        The rating is back to what recognition alone supports."""
        great_fire = next(t for t in topics.CORE_TOPICS
                          if "Great Fire of London" in t)
        self.assertEqual(topics.reach_of(great_fire), topics.WIDE)

    def test_measured_winners_are_rated_wide(self):
        for fragment in ("HMS Hood", "Ariane 5 Flight 501", "Hyatt Regency"):
            topic = next(t for t in topics.CORE_TOPICS if fragment in t)
            self.assertEqual(topics.reach_of(topic), topics.WIDE, fragment)

    def test_a_narrow_topic_is_held_back_not_deleted(self):
        """Once the wide lane is used up the pool widens rather than stopping —
        but only then, and it says so."""
        wide = [t for t in topics.CORE_TOPICS + topics.EXPERIMENT_TOPICS
                if topics.reach_of(t) == topics.WIDE]
        picked = topics.pick_topic(exclude=wide)
        self.assertIsNotNone(picked)
        self.assertEqual(topics.reach_of(picked), topics.NARROW)

    def test_saturated_topics_are_never_selected_automatically(self):
        holdback = [t for t in topics.CORE_TOPICS + topics.EXPERIMENT_TOPICS
                    if topics.reach_of(t) != topics.SATURATED]
        self.assertIsNone(topics.pick_topic(exclude=holdback))
        self.assertIsNotNone(topics.pick_topic(exclude=holdback, floor="all"))


class TestMaturityRule(unittest.TestCase):
    """Nothing may judge a subject on an upload that has not settled."""

    def setUp(self):
        import channel_insights
        self.ci = channel_insights
        self._fetch = channel_insights.fetch_recent_videos

    def tearDown(self):
        self.ci.fetch_recent_videos = self._fetch

    def test_a_young_upload_is_not_mature(self):
        self.assertFalse(self.ci.is_mature(_video("yesterday", 24)))
        self.assertTrue(self.ci.is_mature(_video("last month", 24 * 30)))

    def test_unknown_age_counts_as_mature(self):
        self.assertTrue(self.ci.is_mature({"title": "x", "publishedAt": ""}))

    def test_the_encore_lane_ignores_unsettled_shorts(self):
        """A Short published yesterday cannot out-rank a month-old one on likes
        it has not had time to collect — in either direction."""
        young, old = topics.ALL_TOPICS[0], topics.ALL_TOPICS[1]
        self.ci.fetch_recent_videos = lambda *a, **k: [
            _video("Published yesterday", 20, likes=40,
                   description=f"x\n\n{young}\n"),
            _video("Published last month", 24 * 30, likes=25,
                   description=f"x\n\n{old}\n"),
        ]
        self.assertEqual(self.ci.best_performing_topic()[0], old)

    def test_an_all_young_channel_still_produces_a_pick(self):
        """Holding the rule so hard that a new channel gets nothing would be
        worse than the problem it prevents."""
        topic = topics.ALL_TOPICS[0]
        self.ci.fetch_recent_videos = lambda *a, **k: [
            _video("Brand new", 20, likes=9, description=f"x\n\n{topic}\n")]
        self.assertEqual(self.ci.best_performing_topic()[0], topic)


class TestMeasuredSaturation(unittest.TestCase):
    """The static ratings are a prior; this is the live catalogue."""

    def setUp(self):
        import topic_reach
        self.tr = topic_reach

    def test_the_search_query_is_what_someone_would_type(self):
        self.assertEqual(
            self.tr.search_query("The Ariane 5 Flight 501 explosion (1996), "
                                 "destroyed 39 seconds after launch"),
            "Ariane 5 Flight 501 explosion")

    def test_long_videos_are_not_counted_as_shorts(self):
        """videoDuration=short means under FOUR MINUTES, not 'is a Short'."""
        self.assertGreater(self.tr._iso_duration_seconds("PT3M30S"),
                           self.tr.SHORTS_MAX_SECONDS)
        self.assertLessEqual(self.tr._iso_duration_seconds("PT58S"),
                             self.tr.SHORTS_MAX_SECONDS)

    def test_a_failed_measurement_is_not_an_objection(self):
        """No scope, no quota, no API: keep the static rating, never reject."""
        self.tr.ENABLED = True
        try:
            self.assertFalse(self.tr.is_saturated("Anything", youtube=None))
        finally:
            self.tr.ENABLED = True

    def test_vetting_moves_on_from_a_saturated_candidate(self):
        pool = ["saturated one", "clean one"]
        original = self.tr.is_saturated
        self.tr.is_saturated = lambda t, y=None: t == "saturated one"
        try:
            picked, rejected = self.tr.vet(
                lambda excl: next((t for t in pool if t not in excl), None), set())
            self.assertEqual(picked, "clean one")
            self.assertEqual(rejected, ["saturated one"])
        finally:
            self.tr.is_saturated = original

    def test_vetting_stops_before_burning_the_api_quota(self):
        """search.list is 100 units a call against 10,000/day. Three checks."""
        original = self.tr.is_saturated
        self.tr.is_saturated = lambda t, y=None: True
        calls = []
        try:
            self.tr.vet(lambda excl: calls.append(1) or f"topic {len(calls)}", set())
        finally:
            self.tr.is_saturated = original
        self.assertLessEqual(len(calls), self.tr.MAX_CHECKS + 1)


class TestQuestionIsInTheVideo(unittest.TestCase):
    """A comment nobody scrolls to is not shipped. The question is spoken in the
    last seconds and captioned there, which is the only place every viewer is."""

    def setUp(self):
        import main
        self.main = main

    def _script(self, **extra):
        data = {"topic": "The sinking of HMS Hood (1941), the battlecruiser lost",
                "scenes": [{"narration": "The shell found the magazine."},
                           {"narration": "Three men survived."}]}
        data.update(extra)
        return data

    def test_the_question_lands_in_the_final_narration(self):
        script = self._script(comment_question="Whose call sank her?")
        self.main.append_question_to_script(script)
        self.assertTrue(
            script["scenes"][-1]["narration"].endswith("Whose call sank her?"))

    def test_earlier_scenes_are_untouched(self):
        script = self._script(comment_question="Whose call sank her?")
        self.main.append_question_to_script(script)
        self.assertEqual(script["scenes"][0]["narration"],
                         "The shell found the magazine.")

    def test_it_returns_the_same_text_the_comment_uses(self):
        script = self._script(comment_question="Whose call sank her?")
        self.assertEqual(self.main.append_question_to_script(script),
                         "Whose call sank her?")

    def test_running_twice_does_not_duplicate_it(self):
        script = self._script(comment_question="Whose call sank her?")
        self.main.append_question_to_script(script)
        self.main.append_question_to_script(script)
        self.assertEqual(script["scenes"][-1]["narration"].count("Whose call"), 1)

    def test_the_switch_leaves_the_narration_alone(self):
        original = self.main.SPEAK_QUESTION
        self.main.SPEAK_QUESTION = False
        try:
            script = self._script(comment_question="Whose call sank her?")
            returned = self.main.append_question_to_script(script)
            self.assertEqual(script["scenes"][-1]["narration"],
                             "Three men survived.")
            self.assertEqual(returned, "Whose call sank her?")
        finally:
            self.main.SPEAK_QUESTION = original


class TestFirstComment(unittest.TestCase):
    """Zero comments across 50 videos, because nothing ever asked for one."""

    def test_the_models_question_is_used_when_it_gives_one(self):
        import main
        self.assertEqual(
            main.build_first_comment({"comment_question": "Who do you blame, "
                                      "Nolan or Lucan?", "topic": "x"}),
            "Who do you blame, Nolan or Lucan?")

    def test_a_missing_question_falls_back_to_naming_the_subject(self):
        import main
        text = main.build_first_comment(
            {"topic": "The sinking of HMS Hood (1941), the battlecruiser lost"})
        self.assertIn("HMS Hood", text)
        self.assertTrue(text.endswith("?"))

    def test_nothing_to_say_posts_nothing(self):
        import main
        self.assertEqual(main.build_first_comment({}), "")

    def test_an_empty_comment_is_never_posted(self):
        import youtube_publisher
        self.assertFalse(youtube_publisher.post_first_comment(None, "vid", "  "))

    def test_both_pipelines_ask_for_a_comment(self):
        import inspect, longform_main, main
        self.assertIn("first_comment=", inspect.getsource(main.main))
        self.assertIn("first_comment=", inspect.getsource(longform_main.main))


class TestSeriesIdentity(unittest.TestCase):

    def test_numbering_continues_from_the_channel(self):
        history = [_video("Catastrophe Files #7 — A Thing #shorts", 10),
                   _video("Catastrophe Files #6 — Another #shorts", 34)]
        self.assertEqual(ph.next_episode_number(history, "Catastrophe Files"), 8)

    def test_first_episode_when_none_are_published(self):
        self.assertEqual(
            ph.next_episode_number([_video("An old title", 5)],
                                   "Catastrophe Files"), 1)

    def test_unreadable_history_gives_no_number_rather_than_a_guess(self):
        self.assertIsNone(ph.next_episode_number(None, "Catastrophe Files"))

    def test_suffix_forms(self):
        self.assertEqual(ph.series_suffix("Catastrophe Files", 3),
                         " — Catastrophe Files No. 3")
        self.assertEqual(ph.series_suffix("Catastrophe Files", None),
                         " — Catastrophe Files")
        self.assertEqual(ph.series_suffix("", 3), "")

    def test_numbering_survives_the_move_from_prefix_to_suffix(self):
        """The first fifty titles carry the marker in front and everything
        after carries it at the end. The count must not restart at 1."""
        history = [_video("The Bolt That Sank Her — Catastrophe Files No. 51", 2),
                   _video("Catastrophe Files #50 — An Older One #shorts", 40)]
        self.assertEqual(ph.next_episode_number(history, "Catastrophe Files"), 52)

    def test_recent_titles_are_newest_first(self):
        history = [_video("old", 50), _video("new", 1)]
        self.assertEqual(ph.recent_titles(history), ["new", "old"])


class TestShortsTitleBudget(unittest.TestCase):

    def setUp(self):
        import main
        self.main = main

    def test_series_marker_and_tag_fit_inside_youtubes_limit(self):
        series = ph.series_suffix("Catastrophe Files", 128)
        title = self.main.sanitize_title("A" * 200, series)
        self.assertLessEqual(len(title), 100)
        self.assertTrue(title.endswith(f"{series} #shorts"))

    def test_a_normal_title_survives_intact(self):
        series = ph.series_suffix("Catastrophe Files", 14)
        body = "The Design Flaw That Destroyed HMS Hood"
        self.assertEqual(self.main.sanitize_title(body, series),
                         f"{body}{series} #shorts")

    def test_the_story_owns_the_front_of_the_title(self):
        """The Shorts feed truncates around 40 characters. As a prefix the
        series marker spent the first 24 of them on text identical across all
        fifty videos, so the visible title never reached the subject."""
        series = ph.series_suffix("Catastrophe Files", 14)
        title = self.main.sanitize_title(
            "The Math Error That Starved Air Canada Flight 143", series)
        self.assertTrue(title.startswith("The Math Error"))
        self.assertNotIn("Catastrophe Files", title[:40])

    def test_the_writer_is_asked_for_a_title_that_actually_fits(self):
        budget = self.main.title_budget(ph.series_suffix("Catastrophe Files", 14))
        self.assertGreater(budget, 40)
        self.assertLessEqual(budget + len(" — Catastrophe Files No. 14")
                             + len(" #shorts"), 100)

    def test_the_episode_number_is_not_a_hashtag(self):
        """YouTube reads any '#' in a title or description as a hashtag and
        renders the first three above the video. '#14' was arriving ahead of
        the three the channel chose."""
        title = self.main.sanitize_title(
            "A Story", ph.series_suffix("Catastrophe Files", 14))
        self.assertIn("No. 14", title)
        self.assertNotIn("#14", title)

    def test_the_description_states_the_series(self):
        script = {"title": "A Title", "description": "Two sentences.",
                  "topic": "A topic", "tags": ["x"],
                  "scenes": [{"narration": "n"}]}
        _, description, _ = self.main.build_metadata(script, episode=9)
        self.assertIn("Catastrophe Files No. 9", description)


class TestPlanRunStandsDown(unittest.TestCase):
    """A run that would publish a second video hours after the first must stop
    before it spends a single model request."""

    def setUp(self):
        import main
        self.main = main
        self._loader = main.load_published

    def tearDown(self):
        self.main.load_published = self._loader

    def test_recent_upload_stops_the_run_cleanly(self):
        self.main.load_published = lambda *a, **k: [
            _video("The Arrogant Mayor Who Burned London", 1.7)]
        with self.assertRaises(SystemExit) as caught:
            self.main.plan_run()
        # Exit 0, not 1: standing down is the correct outcome, not a failure.
        self.assertEqual(caught.exception.code, 0)

    def test_no_history_still_produces_a_topic(self):
        self.main.load_published = lambda *a, **k: None
        topic, avoid, episode = self.main.plan_run()
        self.assertTrue(topic)
        self.assertIsNone(episode)

    def test_a_clear_day_plans_an_unpublished_topic(self):
        covered = topics.CORE_TOPICS[0]
        self.main.load_published = lambda *a, **k: [
            _video("An old one", 200, description=f"x\n\n{covered}\n")]
        topic, avoid, episode = self.main.plan_run()
        self.assertNotEqual(topic, covered)
        self.assertEqual(episode, 1)
        self.assertEqual(avoid, ["An old one"])


class TestWriterAvoidsPublishedTitles(unittest.TestCase):

    def test_title_limit_is_enforced_against_the_caller_s_budget(self):
        body = ("The written order named no target at all, and the officer who "
                "carried it across the valley pointed at the wrong guns "
                "entirely on that cold October morning.")
        data = {"title": "T" * 70,
                "scenes": [{"narration": "Six hundred cavalrymen charged the "
                                         "wrong valley.", "image_prompt": "p"}]
                          + [{"narration": body, "image_prompt": "p"}
                             for _ in range(4)]}
        self.assertTrue(llm_engine._validate(data, title_max=80))
        self.assertFalse(llm_engine._validate(data, title_max=60))

    def test_a_duplicate_report_from_the_writer_aborts_the_script(self):
        import inspect
        src = inspect.getsource(llm_engine.generate_video_script)
        self.assertIn("ALREADY PUBLISHED ON THIS CHANNEL", src)
        self.assertIn('data.get("duplicate")',
                      inspect.getsource(llm_engine.generate_video_script))


class TestEncoreLaneCannotRepeatItself(unittest.TestCase):
    """The most-liked Short does not change week to week, so without this the
    encore lane remakes the same video every Tuesday."""

    def setUp(self):
        import channel_insights
        self.ci = channel_insights
        self._fetch = channel_insights.fetch_recent_videos

    def tearDown(self):
        self.ci.fetch_recent_videos = self._fetch

    def test_a_topic_already_covered_in_longform_is_skipped(self):
        top, second = topics.ALL_TOPICS[0], topics.ALL_TOPICS[1]
        self.ci.fetch_recent_videos = lambda *a, **k: [
            _video("Top Short", 100, description=f"x\n\n{top}\n", likes=30),
            _video("Runner Up", 90, description=f"x\n\n{second}\n", likes=20),
            _video("The deep dive", 80, duration="PT7M50S",
                   description=f"x\n\n{top}\n"),
        ]
        picked = self.ci.best_performing_topic()
        self.assertIsNotNone(picked)
        self.assertEqual(picked[0], second)

    def test_without_a_longform_copy_the_top_short_wins(self):
        top = topics.ALL_TOPICS[0]
        self.ci.fetch_recent_videos = lambda *a, **k: [
            _video("Top Short", 100, description=f"x\n\n{top}\n", likes=30)]
        self.assertEqual(self.ci.best_performing_topic()[0], top)


class TestTrendingLaneStaysOnNiche(unittest.TestCase):
    """The trend picks the subject; the niche picks which story about it gets
    told. Both published timely videos failed that second half."""

    def test_a_decision_story_passes(self):
        self.assertTrue(longform_llm._anchor_fits_niche(
            "the 1918 decision to scuttle the Zeebrugge blockships in the wrong "
            "position, which left the canal open"))

    def test_the_swiss_bunker_law_is_rejected(self):
        self.assertFalse(longform_llm._anchor_fits_niche(
            "the 1963 Swiss civil protection law requiring a fallout shelter in "
            "every new house"))

    def test_the_connection_sentence_can_carry_the_niche(self):
        self.assertTrue(longform_llm._anchor_fits_niche(
            "the 1415 capture of Ceuta",
            "Portugal chose to hold a city it could not supply and lost the "
            "trade route it wanted"))

    def test_the_prompt_names_the_format_and_the_avoid_list(self):
        import inspect
        src = inspect.getsource(longform_llm.select_historical_anchor)
        self.assertIn("THIS CHANNEL'S KIND OF STORY", src)
        self.assertIn("ALREADY COVERED ON THIS CHANNEL", src)


# ---------------------------------------------------------------------------
# The distribution layer: the transcript, the playlist, the thumbnail and the
# end card. None of these change what the video says; all of them change
# whether anyone is handed a second one.
# ---------------------------------------------------------------------------


def _timed(narration, start=0.0, per_word=0.4):
    """Word timings shaped like the ones edge-tts emits."""
    words = []
    t = start
    for w in narration.split():
        words.append({"word": w, "start": t, "end": t + per_word})
        t += per_word
    return words


def _scene(narration, duration=None, timed=True):
    scene = {"narration": narration}
    scene["duration"] = duration if duration is not None else \
        len(narration.split()) * 0.4
    if timed:
        scene["words"] = _timed(narration)
    return scene


class TestTranscriptCues(unittest.TestCase):
    """The burned-in captions are pixels. This is the copy YouTube can read."""

    def test_cue_breaks_on_the_sentence_not_mid_clause(self):
        scene = _scene("The ship sank. Twenty minutes had passed.")
        cues = caption_track.build_cues([scene], [0.0])
        self.assertEqual([c[2] for c in cues],
                         ["The ship sank.", "Twenty minutes had passed."])

    def test_a_long_sentence_is_split_rather_than_left_unreadable(self):
        long_line = " ".join(["word"] * 30) + "."
        cues = caption_track.build_cues([_scene(long_line)], [0.0])
        self.assertGreater(len(cues), 1)
        for _, _, text in cues:
            self.assertLessEqual(len(text.split()), caption_track.MAX_CUE_WORDS)

    def test_cues_are_offset_onto_the_finished_timeline(self):
        scenes = [_scene("First scene here."), _scene("Second scene here.")]
        cues = caption_track.build_cues(scenes, [0.0, 10.0])
        self.assertAlmostEqual(cues[0][0], 0.0, places=2)
        self.assertAlmostEqual(cues[1][0], 10.0, places=2)

    def test_crossfaded_scenes_do_not_produce_overlapping_cues(self):
        # Scene 2 starts before scene 1's audio ends, which is what a crossfade
        # is. An SRT whose cues overlap stutters in the player.
        scenes = [_scene("One two three four."), _scene("Five six seven eight.")]
        cues = caption_track.build_cues(scenes, [0.0, 1.0])
        for earlier, later in zip(cues, cues[1:]):
            self.assertLessEqual(earlier[1], later[0] + 1e-6)

    def test_a_scene_without_timings_still_appears(self):
        """gTTS fallback scenes carry no WordBoundary events. A transcript with
        a hole in the middle is worse than an approximate one."""
        scene = _scene("The order was given.", duration=4.0, timed=False)
        cues = caption_track.build_cues([scene], [0.0])
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0][2], "The order was given.")
        self.assertLessEqual(cues[0][1], 4.0 + 1e-6)

    def test_transcript_keeps_sentence_case(self):
        """The on-screen captions are upper-cased for legibility. A transcript
        is read by people and parsers, and neither wants shouting."""
        cues = caption_track.build_cues([_scene("The Vasa sank.")], [0.0])
        self.assertEqual(cues[0][2], "The Vasa sank.")


class TestSrtFormat(unittest.TestCase):

    def test_timestamps_use_the_srt_comma(self):
        self.assertEqual(caption_track._srt_time(3661.5), "01:01:01,500")
        self.assertEqual(caption_track._srt_time(0), "00:00:00,000")

    def test_negative_time_is_clamped_not_rendered(self):
        self.assertEqual(caption_track._srt_time(-2), "00:00:00,000")

    def test_blocks_are_numbered_from_one_and_well_formed(self):
        scenes = [_scene("First line here. Second line here.")]
        srt = caption_track.build_srt(scenes, [0.0])
        blocks = [b for b in srt.strip().split("\n\n") if b.strip()]
        self.assertEqual(len(blocks), 2)
        for i, block in enumerate(blocks, start=1):
            lines = block.strip().split("\n")
            self.assertEqual(lines[0], str(i))
            self.assertRegex(lines[1],
                             r"^\d{2}:\d{2}:\d{2},\d{3} --> "
                             r"\d{2}:\d{2}:\d{2},\d{3}$")

    def test_write_srt_returns_none_rather_than_raising_on_junk(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(caption_track.write_srt([{"narration": ""}],
                                                      [0.0], tmp))

    def test_write_srt_produces_a_file_the_uploader_can_find(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = caption_track.write_srt([_scene("A ship sank.")], [0.0], tmp)
            self.assertEqual(os.path.basename(path), caption_track.SRT_NAME)
            self.assertIn("A ship sank.", open(path, encoding="utf-8").read())


class TestBothPipelinesWriteATranscript(unittest.TestCase):
    """The uploader looks for the file by name. If a render stops writing it,
    the captions silently stop being uploaded — so assert the wiring."""

    def test_shorts_pipeline_writes_the_srt(self):
        import inspect
        self.assertIn("write_srt",
                      inspect.getsource(video_engine.generate_video_pipeline))

    def test_longform_pipeline_writes_the_srt(self):
        import inspect
        self.assertIn("write_srt",
                      inspect.getsource(longform_video.generate_longform_video))

    def test_both_entry_points_pass_the_track_to_the_uploader(self):
        import inspect
        for module in (main, longform_main):
            src = inspect.getsource(module.main)
            self.assertIn("caption_path", src,
                          f"{module.__name__} does not upload its transcript")


class TestPlaylistFiling(unittest.TestCase):
    """9,000 views produced one playlist view, because there was no playlist."""

    class _FakePlaylists:
        def __init__(self, existing):
            self.existing = existing
            self.created = []

        def list(self, **kwargs):
            return _FakeRequest({"items": self.existing})

        def list_next(self, request, response):
            return None

        def insert(self, part=None, body=None):
            self.created.append(body)
            return _FakeRequest({"id": "PL_created"})

    def _youtube(self, existing):
        playlists = self._FakePlaylists(existing)
        return type("YT", (), {"playlists": lambda s: playlists})(), playlists

    def test_an_existing_playlist_is_reused_not_duplicated(self):
        yt, playlists = self._youtube(
            [{"id": "PL_old", "snippet": {"title": "Catastrophe Files"}}])
        self.assertEqual(
            youtube_publisher.ensure_playlist(yt, "Catastrophe Files"), "PL_old")
        self.assertEqual(playlists.created, [])

    def test_the_match_ignores_case_and_stray_whitespace(self):
        yt, playlists = self._youtube(
            [{"id": "PL_old", "snippet": {"title": "  catastrophe files "}}])
        self.assertEqual(
            youtube_publisher.ensure_playlist(yt, "Catastrophe Files"), "PL_old")
        self.assertEqual(playlists.created, [])

    def test_the_playlist_is_created_on_first_use(self):
        yt, playlists = self._youtube([])
        self.assertEqual(
            youtube_publisher.ensure_playlist(yt, "Catastrophe Files"),
            "PL_created")
        self.assertEqual(len(playlists.created), 1)
        self.assertEqual(playlists.created[0]["status"]["privacyStatus"],
                         "public")

    def test_no_title_means_no_playlist_call_at_all(self):
        yt, playlists = self._youtube([])
        self.assertEqual(youtube_publisher.ensure_playlist(yt, ""), "")
        self.assertEqual(playlists.created, [])

    def test_a_playlist_failure_never_raises(self):
        class Broken:
            def playlists(self):
                raise RuntimeError("quota exceeded")
        self.assertEqual(
            youtube_publisher.ensure_playlist(Broken(), "Catastrophe Files"), "")

    def test_adding_to_a_missing_playlist_is_a_no_op(self):
        self.assertFalse(youtube_publisher.add_to_playlist(None, "", "vid123"))


class TestCaptionUploadIsOptional(unittest.TestCase):

    def test_a_missing_file_is_skipped_quietly(self):
        self.assertFalse(
            youtube_publisher.upload_captions(None, "vid", "/no/such/file.srt"))

    def test_an_api_failure_does_not_sink_a_published_video(self):
        class Broken:
            def captions(self):
                raise RuntimeError("insufficient scope")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "captions.srt")
            open(path, "w").write("1\n00:00:00,000 --> 00:00:01,000\nHi\n")
            self.assertFalse(
                youtube_publisher.upload_captions(Broken(), "vid", path))


class TestShortsThumbnailText(unittest.TestCase):
    """Every Short published so far has run on a frame YouTube guessed at."""

    def test_the_writers_headline_is_kept(self):
        data = {"title": "The Order That Killed Six Hundred",
                "thumbnail_text": "THE WRONG VALLEY"}
        llm_engine.normalize_thumbnail_fields(data)
        self.assertEqual(data["thumbnail_text"], "THE WRONG VALLEY")

    def test_a_missing_headline_falls_back_to_the_words_that_carry_meaning(self):
        data = {"title": "The Mistake That Killed the Light Brigade"}
        llm_engine.normalize_thumbnail_fields(data)
        # Not "The Mistake That Killed" — four words of grammar and no fact.
        self.assertEqual(data["thumbnail_text"], "Mistake Killed Light Brigade")

    def test_a_sentence_masquerading_as_a_headline_is_rejected(self):
        data = {"title": "The Bolt That Sank the Ferry",
                "thumbnail_text": "This is the long story of how one single "
                                  "bolt brought down an entire ferry"}
        llm_engine.normalize_thumbnail_fields(data)
        self.assertLessEqual(len(data["thumbnail_text"].split()), 4)

    def test_the_badge_falls_back_to_the_year_in_the_topic(self):
        data = {"title": "The Bolt That Sank the Ferry",
                "topic": "The Herald of Free Enterprise (1987), where the bow "
                         "door was left open"}
        llm_engine.normalize_thumbnail_fields(data)
        self.assertEqual(data["thumbnail_badge"], "1987")

    def test_no_year_anywhere_leaves_the_badge_empty_rather_than_wrong(self):
        data = {"title": "The Bolt That Sank the Ferry", "topic": "no date here"}
        llm_engine.normalize_thumbnail_fields(data)
        self.assertEqual(data["thumbnail_badge"], "")

    def test_the_writer_is_actually_asked_for_the_field(self):
        self.assertIn("thumbnail_text", llm_engine.SYSTEM_PROMPT)

    def test_the_shorts_pipeline_draws_and_uploads_one(self):
        import inspect
        src = inspect.getsource(main.main)
        self.assertIn("build_thumbnail", src)
        self.assertIn("thumbnail_path", src)

    def test_thumbnail_failure_never_costs_the_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A scene pointing at a file that is not an image.
            broken = os.path.join(tmp, "not_an_image.jpg")
            open(broken, "w").write("nope")
            self.assertIsNone(main.build_thumbnail(
                {"scenes": [{"image_path": broken}], "title": "A Title"}, tmp))


class TestLongformEndCard(unittest.TestCase):
    """The seven-minute videos reached the last sentence and simply stopped."""

    def _events(self, total_duration):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "subs.ass")
            longform_video.generate_subtitles(
                [_scene("The port stayed open.")], out, [0.0],
                total_duration=total_duration)
            return open(out, encoding="utf-8").read()

    def test_the_end_card_is_rendered_over_the_closing_seconds(self):
        ass = self._events(120.0)
        self.assertIn(longform_video.CTA_TEXT, ass)
        self.assertIn("Cta,,", ass)

    def test_the_style_the_end_card_uses_exists(self):
        self.assertIn("Style: Cta,", longform_video.ASS_HEADER)

    def test_a_video_shorter_than_the_card_gets_no_card(self):
        self.assertNotIn(longform_video.CTA_TEXT, self._events(1.0))

    def test_the_card_clears_the_narration_captions(self):
        """Both sit at the bottom. If the margins collide the last thing a
        viewer sees is two lines of text on top of each other."""
        body = re.search(r"Style: Body,[^\n]*", longform_video.ASS_HEADER).group(0)
        cta = re.search(r"Style: Cta,[^\n]*", longform_video.ASS_HEADER).group(0)
        self.assertGreater(int(cta.split(",")[-2]), int(body.split(",")[-2]))


class TestTheOpeningIsNotInterrupted(unittest.TestCase):
    """Measured on the published long-form videos: 69% of viewers left inside
    the first 23 seconds, with relative retention at 0.12 — bottom decile. The
    first thing on screen was a card reading PART ONE / COLD OPEN."""

    def test_the_first_chapter_gets_no_title_card(self):
        import inspect
        src = inspect.getsource(longform_video.generate_longform_video)
        # The card is appended only for chapters after the first.
        self.assertRegex(src, r"if c_idx > 0:\s*\n\s*chapter_titles\.append")

    def test_later_chapters_still_get_one(self):
        event = longform_video._chapter_title_event(30.0, 8.0, 3, "The Chain")
        self.assertIn("PART THREE", event)
        self.assertIn("THE CHAIN", event)

    def test_the_opening_heading_is_not_production_jargon(self):
        heading = longform_llm.ARC[0][0]
        # It is typeset in the description's chapter list at 0:00, where a
        # viewer reads it. "Cold Open" is a note to the writer, not to them.
        self.assertNotIn("cold open", heading.lower())

    def test_the_arc_still_starts_where_the_code_thinks_it_does(self):
        """generate_longform_script treats the arc's first chapter as the one
        unrecoverable one. If the arc is reordered — or swapped for a
        format-specific variant — that check must follow it."""
        import inspect
        src = inspect.getsource(longform_llm.generate_longform_script)
        self.assertIn("opening_heading = arc[0][0]", src)
        self.assertIn("if heading == opening_heading:", src)


class TestLongformOpeningHook(unittest.TestCase):
    """The Shorts have been hook-validated since the view decline was traced to
    weak first lines. The long-form lane had no such check at all."""

    def _chapter(self, first_line):
        return {"segments": [{"narration": first_line + " The rest follows."}]}

    def test_a_concrete_opening_passes(self):
        self.assertTrue(longform_llm._opening_hook_ok(self._chapter(
            "Six hundred cavalrymen charged the wrong valley.")))

    def test_a_date_first_opening_is_rejected(self):
        self.assertFalse(longform_llm._opening_hook_ok(self._chapter(
            "In 1854, the British army was camped above Balaclava.")))

    def test_an_abstract_opening_is_rejected(self):
        self.assertFalse(longform_llm._opening_hook_ok(self._chapter(
            "A terrible mistake was made that day.")))

    def test_a_rambling_opening_is_rejected(self):
        self.assertFalse(longform_llm._opening_hook_ok(self._chapter(
            "The Charge of the Light Brigade is remembered today as one of the "
            "most famous military blunders in all of recorded history.")))

    def test_an_empty_chapter_is_not_a_valid_hook(self):
        self.assertFalse(longform_llm._opening_hook_ok({"segments": []}))
        self.assertFalse(longform_llm._opening_hook_ok(None))

    def test_the_opening_sentence_stops_at_the_full_stop(self):
        self.assertEqual(
            longform_llm._opening_sentence(self._chapter("The Vasa sank.")),
            "The Vasa sank.")

    def test_a_weak_opening_is_retried_but_never_loses_the_video(self):
        """The opening chapter is the one chapter the format cannot do without,
        so a hook failure must downgrade to 'keep it', not 'drop it'."""
        import inspect
        src = inspect.getsource(longform_llm.generate_longform_script)
        self.assertIn("_opening_hook_ok", src)
        self.assertIn("if best is None:", src)

    def test_the_brief_no_longer_asks_the_opening_to_widen(self):
        brief = longform_llm.ARC[0][1]
        self.assertNotIn("widen", brief.lower())
        self.assertIn("never pose the question", brief.lower())

    def test_the_opening_chapter_is_shorter_than_the_body_chapters(self):
        """90 words was 33 seconds of runway before chapter two could start."""
        opening_words = longform_llm.ARC[0][2]
        self.assertLessEqual(opening_words, 75)
        for _, _, words in longform_llm.ARC[1:]:
            self.assertGreater(words, opening_words)


class TestPlaylistReconciler(unittest.TestCase):
    """The back catalogue was published before anything could file it, and
    add_to_playlist swallows its failures by design, so drift is permanent
    without something that goes looking for it."""

    def _youtube(self, uploaded, filed, private=()):
        """A stand-in for the two playlistItems listings the tool reads."""
        uploads_items = [
            {"contentDetails": {"videoId": v},
             "status": {"privacyStatus": "private" if v in private else "public"}}
            for v in uploaded]
        filed_items = [{"contentDetails": {"videoId": v}} for v in filed]

        class Items:
            def list(self, part=None, playlistId=None, maxResults=None):
                items = uploads_items if playlistId == "UU_uploads" else filed_items
                return _FakeRequest({"items": items})

            def list_next(self, request, response):
                return None

        class Channels:
            def list(self, part=None, mine=None):
                return _FakeRequest({"items": [{"contentDetails": {
                    "relatedPlaylists": {"uploads": "UU_uploads"}}}]})

        return type("YT", (), {"playlistItems": lambda s: Items(),
                               "channels": lambda s: Channels()})()

    def test_uploads_are_returned_oldest_first(self):
        """The uploads playlist is newest-first. A series should read in the
        order it was published, and new uploads append from the end."""
        yt = self._youtube(["newest", "middle", "oldest"], [])
        self.assertEqual(reconcile_playlist.uploaded_video_ids(yt),
                         ["oldest", "middle", "newest"])

    def test_private_videos_are_skipped(self):
        """They cannot be added to a playlist, and trying costs a unit and an
        error apiece."""
        yt = self._youtube(["a", "b", "c"], [], private=("b",))
        self.assertEqual(reconcile_playlist.uploaded_video_ids(yt),
                         ["c", "a"])

    def test_already_filed_videos_are_read_back(self):
        yt = self._youtube(["a"], ["a", "b"])
        self.assertEqual(reconcile_playlist.playlist_video_ids(yt, "PL_series"),
                         {"a", "b"})

    def test_the_missing_set_is_the_difference(self):
        yt = self._youtube(["c", "b", "a"], ["a"])
        uploads = reconcile_playlist.uploaded_video_ids(yt)
        filed = reconcile_playlist.playlist_video_ids(yt, "PL_series")
        self.assertEqual([v for v in uploads if v not in filed], ["b", "c"])

    def test_a_caught_up_catalogue_has_nothing_to_do(self):
        yt = self._youtube(["b", "a"], ["a", "b"])
        uploads = reconcile_playlist.uploaded_video_ids(yt)
        filed = reconcile_playlist.playlist_video_ids(yt, "PL_series")
        self.assertEqual([v for v in uploads if v not in filed], [])

    def test_a_run_is_capped_so_it_can_share_a_day_with_a_publish(self):
        """Inserts cost 50 units each against a 10,000/day allowance, and a
        publish day already spends ~2,500."""
        self.assertLessEqual(reconcile_playlist.MAX_ADDS * 50, 2000)

    def test_credentials_without_a_channel_stop_rather_than_guess(self):
        class NoChannel:
            def channels(self):
                return type("C", (), {
                    "list": lambda s, part=None, mine=None: _FakeRequest(
                        {"items": []})})()
        with self.assertRaises(SystemExit):
            reconcile_playlist.uploaded_video_ids(NoChannel())


# The channel's real published titles. Six of the eight are the same sentence
# with the nouns changed, which is what these tests exist to stop.
PUBLISHED_TITLES = [
    "Catastrophe Files #12 — The Mistake That Killed the Light Brigade #shorts",
    "Catastrophe Files #11 — The Order That Killed Six Hundred #shorts",
    "Catastrophe Files #10 — Scotland's Colonial Gamble That Destroyed a Nation #shorts",
    "Catastrophe Files #9 — The Math Error That Starved Air Canada Flight 143 #shorts",
    "Catastrophe Files #8 — The Arrogant Mayor Who Burned London #shorts",
    "Catastrophe Files #7 — The Design Flaw That Destroyed HMS Hood #shorts",
    "Soviet Invasion of Kuril Islands 1945: The Hidden WW2 Battle",
    "Zeebrugge Raid 1918: Why the Blockships Failed",
]


class TestSeriesMarkerIsStripped(unittest.TestCase):
    """Shapes have to be read off the title a viewer sees. With the marker left
    on, every historical title starts 'Catastrophe' and shapes identically for
    the wrong reason."""

    def test_a_prefix_marker_is_removed(self):
        self.assertEqual(
            llm_engine.strip_series_marker(
                "Catastrophe Files #12 — The Mistake That Killed the Light "
                "Brigade #shorts", "Catastrophe Files"),
            "The Mistake That Killed the Light Brigade")

    def test_a_suffix_marker_is_removed(self):
        self.assertEqual(
            llm_engine.strip_series_marker(
                "The Bolt That Sank Her — Catastrophe Files #51 #shorts",
                "Catastrophe Files"),
            "The Bolt That Sank Her")

    def test_an_unnumbered_marker_is_removed(self):
        self.assertEqual(
            llm_engine.strip_series_marker(
                "Catastrophe Files — A Title", "Catastrophe Files"), "A Title")

    def test_trailing_hashtags_go_but_the_title_survives(self):
        self.assertEqual(
            llm_engine.strip_series_marker("A Real Title #shorts #history", ""),
            "A Real Title")

    def test_a_title_without_a_marker_is_untouched(self):
        self.assertEqual(
            llm_engine.strip_series_marker("Zeebrugge Raid 1918: Why the "
                                           "Blockships Failed",
                                           "Catastrophe Files"),
            "Zeebrugge Raid 1918: Why the Blockships Failed")


class TestTitleShape(unittest.TestCase):

    def _shape(self, title):
        return llm_engine.title_shape(
            llm_engine.strip_series_marker(title, "Catastrophe Files"))

    def test_the_house_formula_is_recognised(self):
        for title in PUBLISHED_TITLES[:6]:
            self.assertEqual(self._shape(title), "x-that-y", title)

    def test_irregular_verbs_count_too(self):
        """'That Sank' has no -ed and is the same formula."""
        self.assertEqual(llm_engine.title_shape("The Bolt That Sank Her"),
                         "x-that-y")

    def test_question_led_is_its_own_shape(self):
        self.assertEqual(
            llm_engine.title_shape("Why 600 Cavalry Charged the Wrong Valley"),
            "question-led")

    def test_number_led_is_its_own_shape(self):
        self.assertEqual(llm_engine.title_shape("8,000 Men Died in a Hole"),
                         "number-led")

    def test_a_plain_statement_is_declarative(self):
        self.assertEqual(
            llm_engine.title_shape("Russia's Fleet Fired on British Fishermen"),
            "declarative")


class TestTitleValidator(unittest.TestCase):
    """The prompt used to instruct the formula outright, with three worked
    examples. This is the backstop that stops it returning by habit."""

    def test_a_sixteenth_title_in_the_same_shape_is_rejected(self):
        self.assertFalse(llm_engine.validate_title(
            "The Bolt That Sank the Ferry", PUBLISHED_TITLES,
            "Catastrophe Files"))

    def test_a_different_shape_passes(self):
        self.assertTrue(llm_engine.validate_title(
            "Why 600 Cavalry Charged the Wrong Valley", PUBLISHED_TITLES,
            "Catastrophe Files"))

    def test_vague_significance_is_rejected_however_it_is_dressed(self):
        for bad in ("Soviet Invasion of Kuril Islands 1945: The Hidden WW2 Battle",
                    "The Untold Story of the Vasa",
                    "What Really Happened at Dogger Bank",
                    "The Forgotten Disaster of 1904",
                    "The SHOCKING Truth About HMS Hood"):
            self.assertFalse(llm_engine.validate_title(bad), bad)

    def test_the_formula_is_not_banned_only_capped(self):
        """Used occasionally it is a good title shape. The rule is that it
        cannot be every title."""
        varied = ["Why the Blockships Failed", "8,000 Men Died in a Hole",
                  "Russia's Fleet Fired on Fishermen",
                  "The Bolt That Sank Her", "How One Word Killed 600",
                  "Sweden's Flagship Never Left the Harbour",
                  "One Missing Bolt", "The Order Nobody Questioned"]
        self.assertTrue(llm_engine.validate_title(
            "The Mistake That Killed the Light Brigade", varied, ""))

    def test_an_empty_title_is_never_valid(self):
        self.assertFalse(llm_engine.validate_title("", PUBLISHED_TITLES))
        self.assertFalse(llm_engine.validate_title(None, PUBLISHED_TITLES))

    def test_a_new_channel_is_not_judged_on_two_titles(self):
        """Saturation needs a window worth measuring; rejecting on a sample of
        two would just be noise."""
        self.assertTrue(llm_engine.validate_title(
            "The Bolt That Sank the Ferry",
            ["The Order That Killed Six Hundred"], ""))

    def test_the_writer_is_warned_about_the_rut_it_is_in(self):
        warning = llm_engine._shape_warning(PUBLISHED_TITLES, "Catastrophe Files")
        self.assertIn("6 of the last 8", warning)

    def test_no_warning_when_the_titles_already_vary(self):
        varied = ["Why the Blockships Failed", "8,000 Men Died in a Hole",
                  "Russia's Fleet Fired on Fishermen", "One Missing Bolt",
                  "How One Word Killed 600", "The Order Nobody Questioned"]
        self.assertEqual(llm_engine._shape_warning(varied, ""), "")

    def test_the_prompt_no_longer_prescribes_a_single_shape(self):
        """The root cause: the system prompt said titles that perform 'follow
        the shape the <decision> that <destroyed something>' and gave three
        examples of it."""
        prompt = llm_engine.SYSTEM_PROMPT
        self.assertNotIn("follow the", prompt.lower())
        self.assertIn("VARY THE SHAPE", prompt)

    def test_the_script_validator_enforces_the_title_rules(self):
        script = {
            "title": "Soviet Invasion of Kuril Islands: The Hidden WW2 Battle",
            "scenes": [{"narration": "Six hundred cavalrymen charged the wrong "
                                     "valley because of one word.",
                        "image_prompt": "x"}] +
                      [{"narration": " ".join(["word"] * 30),
                        "image_prompt": "x"} for _ in range(4)],
        }
        self.assertFalse(llm_engine._validate(script, 80, PUBLISHED_TITLES,
                                              "Catastrophe Files"))


class TestLongformRetitles(unittest.TestCase):

    def test_a_rejected_title_gets_one_corrective_retry(self):
        import inspect
        src = inspect.getsource(longform_llm.generate_longform_script)
        self.assertIn("retitle=True", src)

    def test_the_retry_names_the_failure_rather_than_repeating_the_brief(self):
        import inspect
        src = inspect.getsource(longform_llm._packaging_call)
        self.assertIn("YOUR PREVIOUS TITLE WAS REJECTED", src)
        self.assertIn("not a rewording", src)

    def test_the_packaging_prompt_demands_a_comprehensible_title(self):
        import inspect
        src = inspect.getsource(longform_llm._packaging_call)
        self.assertIn("NEVER HEARD OF", src)
        self.assertIn("Dogger Bank", src)   # the worked example of the failure


class TestEpisodeFormats(unittest.TestCase):
    """The pool is varied — the Emu War, the Dancing Plague, a Bronze Age
    computer — and every one was narrated as 'one decision and what it cost'."""

    def test_every_pool_topic_has_a_format(self):
        for topic in topics.CORE_TOPICS + topics.EXPERIMENT_TOPICS:
            self.assertIn(topics.format_of(topic), formats.FORMATS, topic)

    def test_the_two_core_groups_map_to_their_formats(self):
        self.assertEqual(topics.format_of(topics._ORDERS[0][0]), "order")
        self.assertEqual(topics.format_of(topics._DESIGNS[0][0]), "design")
        self.assertEqual(topics.format_of(topics._EXPERIMENT[0][0]), "unsolved")

    def test_published_topics_are_tagged_by_hand_not_by_keyword(self):
        """A keyword pass put the Ghost Army under DESIGN because its decoys
        were inflatable tanks, and the Battle of Los Angeles under DESIGN
        because the guns 'fired'."""
        ghost = next(t for t in topics.PUBLISHED_TOPICS if "Ghost Army" in t)
        la = next(t for t in topics.PUBLISHED_TOPICS if "Battle of Los Angeles" in t)
        self.assertEqual(topics.format_of(ghost), "order")
        self.assertEqual(topics.format_of(la), "unsolved")

    def test_the_mysteries_among_the_published_are_unsolved(self):
        for fragment in ("Jim Twins", "Dancing Plague", "Antikythera",
                         "Dyatlov", "Dennis the Menace"):
            topic = next(t for t in topics.PUBLISHED_TOPICS if fragment in t)
            self.assertEqual(topics.format_of(topic), "unsolved", fragment)

    def test_an_unknown_topic_still_gets_a_format(self):
        """The trending lane invents its own topics from free text."""
        self.assertEqual(topics.format_of("something nobody listed"), "order")
        self.assertEqual(topics.format_of(""), "order")
        self.assertEqual(topics.format_of(None), "order")

    def test_each_format_is_visually_distinct(self):
        accents = [f["accent"] for f in formats.FORMATS.values()]
        self.assertEqual(len(accents), len(set(accents)))

    def test_an_unknown_format_key_falls_back_rather_than_raising(self):
        self.assertEqual(formats.get("nonsense"), formats.FORMATS["order"])
        self.assertEqual(formats.name_of(None), "THE ORDER")
        self.assertTrue(formats.accent_of(""))

    def test_every_format_carries_what_the_pipeline_reads(self):
        for key, cfg in formats.FORMATS.items():
            for field in ("name", "accent", "promise", "brief", "tags"):
                self.assertIn(field, cfg, key)
            self.assertEqual(len(cfg["accent"]), 3, key)
            self.assertTrue(cfg["tags"], key)


class TestFormatAwareArc(unittest.TestCase):
    """An UNSOLVED story has no decision to put at the middle of the arc. The
    Antikythera Mechanism already became a long-form video that had to pretend
    a shipwreck was somebody's fault."""

    def test_order_and_design_keep_the_standard_arc(self):
        self.assertIs(longform_llm.arc_for("order"), longform_llm.ARC)
        self.assertIs(longform_llm.arc_for("design"), longform_llm.ARC)

    def test_unsolved_replaces_the_decision_chapter(self):
        headings = [h for h, _, _ in longform_llm.arc_for("unsolved")]
        self.assertNotIn("The Decision", headings)
        self.assertIn("What Has Been Ruled Out", headings)

    def test_unsolved_replaces_the_counterfactual(self):
        headings = [h for h, _, _ in longform_llm.arc_for("unsolved")]
        self.assertNotIn("What Would Have Stopped It", headings)
        self.assertIn("What Is Still Open", headings)

    def test_the_arc_keeps_its_length_and_opening(self):
        standard, unsolved = longform_llm.ARC, longform_llm.arc_for("unsolved")
        self.assertEqual(len(standard), len(unsolved))
        self.assertEqual(standard[0][0], unsolved[0][0])

    def test_the_substituted_chapters_keep_their_word_budgets(self):
        by_heading = {h: w for h, _, w in longform_llm.ARC}
        for heading, (_, _, words) in longform_llm._UNSOLVED_SUBSTITUTIONS.items():
            self.assertEqual(words, by_heading[heading], heading)

    def test_an_unknown_format_gets_the_standard_arc(self):
        self.assertIs(longform_llm.arc_for(None), longform_llm.ARC)


class TestFormatReachesTheViewer(unittest.TestCase):
    """A format only exists if it can be seen."""

    def test_the_thumbnail_takes_a_format_accent(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "s.jpg")
            Image.new("RGB", (1080, 1920), (20, 20, 30)).save(src)
            outs = []
            for key in ("order", "design", "unsolved"):
                out = os.path.join(tmp, f"{key}.jpg")
                thumbnail_engine.make_thumbnail(
                    src, "ONE MISSING BOLT", out,
                    badge=formats.name_of(key),
                    accent=formats.accent_of(key))
                outs.append(open(out, "rb").read())
            # Three different accents must produce three different images.
            self.assertEqual(len(set(outs)), 3)

    def test_the_thumbnail_still_works_without_a_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "s.jpg")
            Image.new("RGB", (1080, 1920), (20, 20, 30)).save(src)
            out = os.path.join(tmp, "o.jpg")
            thumbnail_engine.make_thumbnail(src, "A HEADLINE", out)
            self.assertTrue(os.path.getsize(out) > 0)

    def test_the_format_reaches_the_tags(self):
        script = {"title": "A Title", "description": "Two sentences.",
                  "topic": "A topic", "tags": ["x"], "format": "unsolved",
                  "scenes": [{"narration": "n"}]}
        _, _, tags = main.build_metadata(script, episode=9)
        self.assertIn("unsolved mysteries", tags)

    def test_the_description_states_the_promise(self):
        script = {"title": "A Title", "description": "Two sentences.",
                  "topic": "A topic", "tags": ["x"], "format": "design",
                  "scenes": [{"narration": "n"}]}
        _, description, _ = main.build_metadata(script, episode=9)
        self.assertIn("THE DESIGN", description)
        self.assertIn(formats.get("design")["promise"], description)

    def test_metadata_survives_a_script_with_no_format(self):
        script = {"title": "A Title", "description": "Two sentences.",
                  "topic": "A topic", "tags": ["x"],
                  "scenes": [{"narration": "n"}]}
        _, description, tags = main.build_metadata(script, episode=1)
        self.assertIn("THE ORDER", description)
        self.assertTrue(tags)


def _chapter(*sentences):
    """A chapter shaped the way the validators expect to receive one."""
    return {"segments": [{"narration": t, "image_prompt": "a shot"}
                         for t in sentences]}


class TestChaptersHandOffToTheNextOne(unittest.TestCase):
    """Every chapter used to be written to feel finished, and each seam was a
    place the viewer could leave without abandoning anything. A chapter now
    ends on a fact the viewer cannot resolve on their own."""

    def test_an_unresolved_fact_is_a_valid_handoff(self):
        self.assertTrue(longform_llm._handoff_ok(
            _chapter("The fleet sailed.", "The order had already been signed.")))

    def test_a_name_and_a_distance_leaves_something_open(self):
        self.assertTrue(longform_llm._handoff_ok(
            _chapter("Admiral Tryon was three hours away.")))

    def test_a_chapter_that_concludes_is_rejected(self):
        self.assertFalse(longform_llm._handoff_ok(
            _chapter("In the end, the ship was lost with all hands.")))

    def test_a_question_to_the_viewer_is_rejected(self):
        self.assertFalse(longform_llm._handoff_ok(
            _chapter("So what really happened that night?")))

    def test_youtube_tease_phrasing_is_rejected(self):
        """The channel's credibility is the product. A viewer who hears a
        tease stops believing the casualty figures too."""
        for tease in ("But that is not even the strangest part.",
                      "Little did they know what was coming.",
                      "We'll get to that in a moment."):
            with self.subTest(tease=tease):
                self.assertFalse(longform_llm._handoff_ok(_chapter(tease)))

    def test_a_vague_ending_with_nothing_open_is_rejected(self):
        self.assertFalse(longform_llm._handoff_ok(
            _chapter("It was a very bad day for the crew and for the town.")))

    def test_an_empty_chapter_cannot_hand_off(self):
        self.assertFalse(longform_llm._handoff_ok({"segments": []}))

    def test_the_closing_sentence_is_the_last_one_spoken(self):
        chapter = _chapter("First line here.", "Second line. The last one.")
        self.assertEqual(longform_llm._closing_sentence(chapter),
                         "The last one.")

    def test_the_final_chapter_is_told_to_close_instead(self):
        rule = longform_llm._handoff_rule(None)
        self.assertIn("FINAL", rule)
        self.assertNotIn("HAND OFF", rule)

    def test_every_other_chapter_names_the_one_it_leads_into(self):
        rule = longform_llm._handoff_rule("The Decision")
        self.assertIn("HAND OFF", rule)
        self.assertIn("The Decision", rule)

    def test_the_prompt_carries_the_handoff_rule(self):
        prompt = longform_llm._chapter_prompt(
            "A topic", "The Chain", "brief", 280, "",
            next_heading="The Cost")
        self.assertIn("HAND OFF", prompt)
        self.assertIn("The Cost", prompt)


class TestThePacingVaries(unittest.TestCase):
    """One cut rate and one narration speed for seven minutes is why the
    rhythm itself never held anyone: the ear stops being surprised."""

    def test_every_arc_slot_has_a_pacing_entry(self):
        self.assertEqual(len(longform_llm.PACING), len(longform_llm.ARC))

    def test_the_chapters_do_not_all_cut_at_the_same_rate(self):
        rates = {p["words_per_image"] for p in longform_llm.PACING}
        self.assertGreater(len(rates), 3)

    def test_the_chapters_are_not_all_read_at_the_same_speed(self):
        speeds = {p["tts_rate"] for p in longform_llm.PACING}
        self.assertGreater(len(speeds), 3)

    def test_the_midpoint_is_read_slower_than_the_opening(self):
        """Slowing down at the turning point is the interrupt — after two
        chapters at pace, a drop in speed says this is the part that matters."""
        def pct(entry):
            return int(entry["tts_rate"].rstrip("%"))
        self.assertLess(pct(longform_llm.PACING[longform_llm.PIVOT_INDEX]),
                        pct(longform_llm.PACING[0]))

    def test_the_driving_chapter_cuts_fastest(self):
        fastest = min(longform_llm.PACING, key=lambda p: p["words_per_image"])
        self.assertEqual(fastest["role"], "drive")

    def test_an_index_off_the_end_still_returns_a_profile(self):
        self.assertEqual(longform_llm.pacing_for(99),
                         longform_llm.DEFAULT_PACING)

    def test_the_cut_rate_reaches_the_prompt(self):
        tight = longform_llm._chapter_prompt("t", "h", "b", 240, "",
                                             words_per_image=20)
        loose = longform_llm._chapter_prompt("t", "h", "b", 240, "",
                                             words_per_image=40)
        self.assertIn("exactly 12 consecutive segments", tight)
        self.assertIn("exactly 6 consecutive segments", loose)


class TestTheMidpointReset(unittest.TestCase):
    """Retention graphs slump in the middle, and there was nothing in the
    middle to slump against: chapter three looked and sounded like chapter
    two."""

    def _script(self, n=6):
        return {"era_year": 1893, "chapters": [
            {"heading": f"H{i}", "key_stat": {}, "arc_index": i,
             "pacing": longform_llm.PACING[i],
             "scenes": [{"narration": "a"}, {"narration": "b"}]}
            for i in range(n)]}

    def test_exactly_one_chapter_is_the_pivot(self):
        scenes = longform_llm.flatten_scenes(self._script())
        pivots = {s["chapter_index"] for s in scenes if s["is_pivot"]}
        self.assertEqual(pivots, {longform_llm.PIVOT_INDEX})

    def test_the_pivot_follows_the_arc_when_a_chapter_is_dropped(self):
        """A chapter that failed to generate is dropped, so position in the
        finished list is not position in the arc. The reset must land on the
        chapter that earned it, not on whatever ended up third."""
        script = self._script()
        del script["chapters"][1]                      # lose chapter two
        scenes = longform_llm.flatten_scenes(script)
        pivot = [s for s in scenes if s["is_pivot"]]
        self.assertTrue(pivot)
        self.assertEqual(pivot[0]["chapter_heading"], "H2")

    def test_the_narration_takes_a_longer_beat_before_the_pivot(self):
        scenes = longform_llm.flatten_scenes(self._script())
        beats = [s.get("pause_after") for s in scenes if s.get("pause_after")]
        self.assertIn(longform_llm.PIVOT_BEAT, beats)
        self.assertGreater(longform_llm.PIVOT_BEAT, longform_llm.CHAPTER_BEAT)

    def test_every_chapter_ends_on_a_beat(self):
        scenes = longform_llm.flatten_scenes(self._script())
        with_beat = [s for s in scenes if s.get("pause_after")]
        self.assertEqual(len(with_beat), 6)

    def test_the_last_scene_of_the_video_gets_the_closing_beat(self):
        scenes = longform_llm.flatten_scenes(self._script())
        self.assertEqual(scenes[-1]["pause_after"], longform_llm.CLOSING_BEAT)

    def test_the_pivot_card_dims_the_frame_and_centres_the_heading(self):
        events = longform_video._pivot_events(30.0, 9.0, 3, "The Decision")
        joined = "\n".join(events)
        self.assertIn("THE DECISION", joined)
        self.assertIn("PART THREE", joined)
        self.assertIn("Scrim", joined)          # the wash over the frame
        self.assertIn(r"\an5", joined)             # centred, not in the corner

    def test_the_pivot_card_is_bigger_than_an_ordinary_chapter_card(self):
        ordinary = longform_video._chapter_title_event(30.0, 9.0, 4, "The Chain")
        pivot = "\n".join(longform_video._pivot_events(30.0, 9.0, 3, "The Decision"))
        def biggest(text):
            return max(int(m) for m in re.findall(r"\\fs(\d+)", text))
        self.assertGreater(biggest(pivot), biggest(ordinary))

    def test_the_styles_the_pivot_card_uses_exist(self):
        self.assertIn("Style: Pivot,", longform_video.ASS_HEADER)
        self.assertIn("Style: Scrim,", longform_video.ASS_HEADER)

    def test_the_pivot_card_is_rendered_when_the_cue_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "subs.ass")
            longform_video.generate_subtitles(
                [_scene("A line.")], out, [0.0],
                chapter_titles=[(10.0, 9.0, 3, "The Decision", True)])
            self.assertIn("Pivot,,", open(out, encoding="utf-8").read())

    def test_an_ordinary_cue_still_gets_the_ordinary_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "subs.ass")
            longform_video.generate_subtitles(
                [_scene("A line.")], out, [0.0],
                chapter_titles=[(10.0, 9.0, 4, "The Chain", False)])
            ass = open(out, encoding="utf-8").read()
            self.assertIn("Chapter,,", ass)
            self.assertNotIn("Pivot,,", ass)


class TestTheSoundtrackMoves(unittest.TestCase):
    """Narration plus one music file at a fixed 10% for seven minutes. Not one
    auditory event in the whole video — which is everything, for the share of
    the audience that is half-watching in another tab."""

    def test_the_bed_ducks_into_the_midpoint(self):
        expr = longform_video.music_envelope([20.0], 20.0, 300.0)
        self.assertIn(f"-{longform_video.MUSIC_PIVOT_DUCK:.4f}", expr)

    def test_the_bed_comes_back_over_the_top_after_the_duck(self):
        expr = longform_video.music_envelope([20.0], 20.0, 300.0)
        self.assertIn(f"+{longform_video.MUSIC_PIVOT_SWELL:.4f}", expr)

    def test_the_pivot_does_not_also_get_an_ordinary_card_lift(self):
        """Two overlapping moves at the same moment fight each other."""
        expr = longform_video.music_envelope([20.0], 20.0, 300.0)
        self.assertNotIn(f"+{longform_video.MUSIC_CARD_LIFT:.4f}", expr)

    def test_ordinary_chapter_cards_still_lift_the_bed(self):
        expr = longform_video.music_envelope([40.0], 200.0, 300.0)
        self.assertIn(f"+{longform_video.MUSIC_CARD_LIFT:.4f}", expr)

    def test_the_level_never_goes_negative_anywhere_in_the_video(self):
        """A negative gain in an ffmpeg volume expression inverts the phase
        rather than silencing it."""
        expr = longform_video.music_envelope([30.0, 60.0, 90.0], 60.0, 400.0)
        env = {"min": min, "max": max}
        for i in range(0, 401):
            value = eval(expr.replace("t", str(float(i))), env)   # noqa: S307
            self.assertGreaterEqual(value, 0.0)

    def test_a_video_with_no_music_still_produces_an_expression(self):
        self.assertTrue(longform_video.music_envelope([], None, 300.0))

    def test_every_number_callout_gets_an_impact_and_every_card_a_riser(self):
        frags, labels = longform_video._sfx_sources(
            [("impact", 10.0), ("riser", 20.0)])
        self.assertEqual(len(labels), 2)
        self.assertIn("adelay=10000|10000", frags[0])
        self.assertIn("adelay=20000|20000", frags[1])

    def test_cues_past_the_end_of_the_video_are_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "sfx.wav")
            self.assertIsNone(
                longform_video.build_sfx_bed([("impact", 500.0)], 100.0, out))

    def test_a_video_with_no_cues_needs_no_sfx_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(longform_video.build_sfx_bed(
                [], 100.0, os.path.join(tmp, "sfx.wav")))

    def test_the_sound_design_stays_under_the_narration(self):
        """The moment a viewer notices the sound design as sound design, the
        channel reads as a template."""
        self.assertLess(longform_video.IMPACT_GAIN, 0.5)
        self.assertLess(longform_video.RISER_GAIN, 0.5)


class TestDeadAirIsRemoved(unittest.TestCase):
    """edge-tts pads every clip it returns. Across forty clips that is most of
    a minute of nothing, delivered as a hesitation at every single cut."""

    def test_word_timings_move_with_the_audio(self):
        words = [{"word": "the", "start": 0.50, "end": 0.70},
                 {"word": "order", "start": 0.80, "end": 1.10}]
        shifted = audio_engine.shift_word_timings(words, 0.40)
        self.assertAlmostEqual(shifted[0]["start"], 0.10)
        self.assertAlmostEqual(shifted[1]["end"], 0.70)

    def test_a_word_inside_the_trimmed_silence_is_clamped_not_dropped(self):
        """Dropping it would lose a caption chunk entirely."""
        shifted = audio_engine.shift_word_timings(
            [{"word": "a", "start": 0.10, "end": 0.20}], 0.40)
        self.assertEqual(len(shifted), 1)
        self.assertEqual(shifted[0]["start"], 0.0)

    def test_nothing_moves_when_nothing_was_trimmed(self):
        words = [{"word": "a", "start": 0.5, "end": 0.7}]
        self.assertIs(audio_engine.shift_word_timings(words, 0.0), words)

    def test_a_clip_that_is_silent_throughout_is_left_alone(self):
        """That is a failed synthesis, not a padded one, and trimming it to
        nothing would take the scene out of the video."""
        self.assertEqual(audio_engine._measure_silence.__doc__ is None, False)


class TestTheOpeningStillDeliversOnWhatItPromised(unittest.TestCase):
    """A viewer burned by an unfulfilled setup does not stay for the next
    one."""

    def test_the_closing_chapter_is_checked_against_the_opening(self):
        script = {"chapters": [
            {"heading": "The Anomaly", "scenes": [
                {"narration": "The Hood exploded in ninety seconds."}]},
            {"heading": "What Would Have Stopped It", "scenes": [
                {"narration": "The Hood needed deck armour she never got."}]}]}
        self.assertTrue(longform_llm.payoff_ok(script))

    def test_an_ending_about_something_else_entirely_is_flagged(self):
        script = {"chapters": [
            {"heading": "The Anomaly", "scenes": [
                {"narration": "The Hood exploded in ninety seconds."}]},
            {"heading": "What Would Have Stopped It", "scenes": [
                {"narration": "Belgrano sailed from Ushuaia that morning."}]}]}
        self.assertFalse(longform_llm.payoff_ok(script))

    def test_a_one_chapter_script_is_not_judged(self):
        script = {"chapters": [{"heading": "The Anomaly",
                                "scenes": [{"narration": "A line."}]}]}
        self.assertTrue(longform_llm.payoff_ok(script))


class TestThePaidTierWearsTheSameInterface(unittest.TestCase):
    """Anthropic does not speak the OpenAI protocol, and this pipeline has
    exactly one call shape. The adapter presents that shape so no call site
    needs to know which provider answered."""

    def _client(self, blocks, stop_reason="end_turn"):
        class Reply:
            def __init__(self):
                self.content = blocks
                self.stop_reason = stop_reason

        class Messages:
            def __init__(self): self.seen = {}
            def create(self, **kwargs):
                self.seen.update(kwargs)
                return Reply()

        class SDK:
            def __init__(self): self.messages = Messages()

        client = llm_provider.AnthropicClient.__new__(llm_provider.AnthropicClient)
        client._sdk = SDK()
        client.chat = client
        client.completions = client
        return client

    def _block(self, kind, text):
        return type("B", (), {"type": kind, "text": text})()

    def test_the_reply_arrives_where_the_pipeline_looks_for_it(self):
        client = self._client([self._block("text", '{"ok": true}')])
        reply = client.chat.completions.create(
            model="m", messages=[{"role": "user", "content": "hi"}],
            max_tokens=100)
        self.assertEqual(reply.choices[0].message.content, '{"ok": true}')

    def test_thinking_blocks_are_not_concatenated_into_the_json(self):
        """A thinking block is not part of the answer, and gluing it onto the
        front of the JSON is what makes the parse fail."""
        client = self._client([self._block("thinking", "hmm, let me think"),
                               self._block("text", '{"ok": true}')])
        reply = client.chat.completions.create(
            messages=[{"role": "user", "content": "hi"}], max_tokens=100)
        self.assertEqual(reply.choices[0].message.content, '{"ok": true}')

    def test_a_truncated_reply_reports_the_reason_the_pipeline_knows(self):
        client = self._client([self._block("text", "{")], stop_reason="max_tokens")
        reply = client.chat.completions.create(
            messages=[{"role": "user", "content": "hi"}], max_tokens=10)
        self.assertEqual(reply.choices[0].finish_reason, "length")

    def test_the_system_prompt_is_lifted_out_of_the_messages(self):
        """Anthropic takes the system prompt as its own parameter; leaving it
        in the message list sends it as a user turn."""
        client = self._client([self._block("text", "{}")])
        client.chat.completions.create(
            messages=[{"role": "system", "content": "You write narration."},
                      {"role": "user", "content": "Write a chapter."}],
            max_tokens=100)
        sent = client._sdk.messages.seen
        self.assertEqual(sent["system"], "You write narration.")
        self.assertEqual([m["role"] for m in sent["messages"]], ["user"])

    def test_it_does_not_spend_the_reply_budget_on_reasoning(self):
        """Thinking tokens come out of the same budget as the reply — the
        exact failure that truncated every chapter of a live Gemini run."""
        client = self._client([self._block("text", "{}")])
        client.chat.completions.create(
            messages=[{"role": "user", "content": "hi"}], max_tokens=100)
        self.assertEqual(
            client._sdk.messages.seen["output_config"]["effort"], "low")

    def test_a_missing_sdk_skips_the_tier_instead_of_ending_the_run(self):
        saved = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        try:
            with unittest.mock.patch.object(
                    llm_provider.AnthropicClient, "__init__",
                    side_effect=ImportError("no module named anthropic")):
                self.assertIsNone(llm_provider.anthropic_client())
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)
            if saved:
                os.environ["ANTHROPIC_API_KEY"] = saved


class TestOneProviderRunningDryDoesNotEndTheRun(unittest.TestCase):
    """A spent OpenRouter allowance used to throw away a working Gemini key
    sitting right beside it — the single point of failure the chain exists to
    remove."""

    def test_the_run_only_stops_when_every_provider_is_out(self):
        src = inspect.getsource(longform_llm._call)
        # The daily-limit branch retires the provider and keeps walking; it
        # raises only once nothing usable is left.
        self.assertIn("_exhausted_providers.add(provider)", src)
        self.assertIn("if not remaining:", src)

    def test_hitting_the_wall_retires_that_provider_not_the_run(self):
        src = inspect.getsource(longform_llm._call)
        quota_branch = src[src.index("if _is_daily_quota(err):"):]
        continued = quota_branch.index("continue")
        raised = quota_branch.index("raise QuotaExhausted")
        # The raise is guarded above the continue; both are present, and the
        # branch ends by carrying on rather than by raising unconditionally.
        self.assertLess(raised, continued)
        self.assertIn("remaining = _usable_models(pool.models)", quota_branch)


class TestNarrationIsNeverMixedIntoNarration(unittest.TestCase):
    """The first published cut of this work had two voices at once for 0.36s
    at every one of its 45 seams. The dead-air trim removed the TTS padding
    that had been absorbing the 0.5s audio crossfade, so the crossfade started
    mixing the end of each sentence into the start of the next."""

    def test_the_audio_is_summed_not_crossfaded(self):
        src = inspect.getsource(longform_video._xfade_chapter)
        self.assertNotIn("acrossfade", src)
        self.assertIn("adelay", src)
        self.assertIn("amix", src)

    def test_the_picture_still_crossfades(self):
        src = inspect.getsource(longform_video._xfade_chapter)
        self.assertIn("xfade=transition=", src)

    def test_a_clip_runs_longer_than_its_narration(self):
        """The overlap the dissolve needs has to be silence, so every clip
        carries XFADE seconds of extra picture and extra quiet."""
        src = inspect.getsource(longform_video._scene_clip)
        self.assertIn("clip_len = duration + XFADE", src)
        self.assertIn("apad", src)

    def test_the_assembler_is_given_those_longer_lengths(self):
        src = inspect.getsource(longform_video.generate_longform_video)
        self.assertIn('g["duration"] + XFADE', src)

    def test_summing_keeps_a_lone_speaking_track_at_its_own_level(self):
        """normalize=1 would divide every line by the number of scenes in the
        chapter, so an 11-scene chapter would be inaudible."""
        src = inspect.getsource(longform_video._xfade_chapter)
        self.assertIn("normalize=0", src)


class TestTheChapterCardIsSilent(unittest.TestCase):
    """Owner's verdict on the first published cut: a swoosh under sliding text
    reads as silly. It was the only sound design that announced itself."""

    def test_no_riser_is_scheduled(self):
        src = inspect.getsource(longform_video.generate_longform_video)
        cue_line = [l for l in src.splitlines() if "sfx_cues" in l]
        self.assertTrue(cue_line)
        self.assertFalse(any("riser" in l for l in cue_line))

    def test_number_callouts_still_get_their_impact(self):
        src = inspect.getsource(longform_video.generate_longform_video)
        self.assertIn('("impact", start)', src)


class TestTheScoreChangesWithTheStory(unittest.TestCase):
    """One bed looped for six minutes, at one level. The midpoint reset was a
    volume move on the same music a viewer had already heard twice."""

    MARKS = [0.0, 25.0, 111.0, 180.0, 250.0, 300.0]

    def test_the_track_changes_at_the_midpoint(self):
        acts = longform_video.plan_score(self.MARKS, 111.0, 350.0, 6)
        starts = [start for _, start, _ in acts]
        self.assertIn(111.0, starts)

    def test_three_acts_not_six(self):
        """A change every chapter is restless; the arc has two boundaries
        worth scoring."""
        acts = longform_video.plan_score(self.MARKS, 111.0, 350.0, 6)
        self.assertEqual(len(acts), 3)

    def test_consecutive_acts_use_different_tracks(self):
        acts = longform_video.plan_score(self.MARKS, 111.0, 350.0, 6)
        for a, b in zip(acts, acts[1:]):
            self.assertNotEqual(a[0], b[0])

    def test_the_acts_cover_the_whole_video_without_gaps(self):
        acts = longform_video.plan_score(self.MARKS, 111.0, 350.0, 6)
        self.assertEqual(acts[0][1], 0.0)
        self.assertAlmostEqual(acts[-1][2], 350.0)
        for a, b in zip(acts, acts[1:]):
            self.assertEqual(a[2], b[1])

    def test_a_single_track_library_still_scores_the_video(self):
        acts = longform_video.plan_score(self.MARKS, 111.0, 350.0, 1)
        self.assertEqual(acts, [(0, 0.0, 350.0)])

    def test_an_empty_music_directory_is_not_an_error(self):
        self.assertEqual(longform_video.plan_score(self.MARKS, 111.0, 350.0, 0), [])

    def test_a_short_video_is_not_chopped_into_stubs(self):
        """Changing the music twice inside ninety seconds is a fidget."""
        acts = longform_video.plan_score([0.0, 30.0, 60.0], 30.0, 80.0, 6)
        self.assertEqual(len(acts), 1)

    def test_every_act_is_long_enough_to_be_worth_a_track(self):
        acts = longform_video.plan_score(self.MARKS, 111.0, 350.0, 6)
        for _, start, end in acts:
            self.assertGreaterEqual(end - start, longform_video.MIN_ACT_SECONDS)

    def test_a_video_with_no_pivot_gets_one_act(self):
        self.assertEqual(
            len(longform_video.plan_score(self.MARKS, None, 350.0, 6)), 1)

class TestSearchMetadata(unittest.TestCase):
    """BASE_TAGS went onto every upload, so a DESIGN story about an airliner
    was tagged 'military history' and an ORDER story about a cavalry charge was
    tagged 'engineering failures'."""

    def _tags(self, fmt):
        script = {"title": "A Title", "description": "Two sentences.",
                  "topic": "A topic", "tags": ["specific term"], "format": fmt,
                  "scenes": [{"narration": "n"}]}
        return main.build_metadata(script, episode=1)[2]

    def test_base_tags_carry_no_format_signal(self):
        for tag in main.BASE_TAGS + longform_main.BASE_TAGS:
            self.assertNotIn("military", tag.lower())
            self.assertNotIn("engineering", tag.lower())

    def test_a_design_episode_is_not_tagged_military(self):
        self.assertNotIn("military history", self._tags("design"))
        self.assertIn("engineering failure", self._tags("design"))

    def test_an_order_episode_is_not_tagged_engineering(self):
        self.assertNotIn("engineering failure", self._tags("order"))
        self.assertIn("military history", self._tags("order"))

    def test_the_scripts_own_terms_come_first(self):
        """Tags are weighted by order, and the specific ones are the only
        reason a small channel appears in search at all."""
        self.assertEqual(self._tags("order")[0], "specific term")

    def test_tags_stay_inside_youtubes_limit(self):
        script = {"title": "A Title", "description": "d", "topic": "t",
                  "format": "order", "tags": ["x" * 60] * 15,
                  "scenes": [{"narration": "n"}]}
        tags = main.build_metadata(script, episode=1)[2]
        self.assertLessEqual(sum(len(t) + 1 for t in tags), 500)


class TestDescriptionHashtags(unittest.TestCase):
    """YouTube counts the first three hashtags found anywhere and renders them
    above the title, so a writer sprinkling its own picked the category."""

    def _description(self, fmt, body):
        script = {"title": "A Title", "description": body, "topic": "A topic",
                  "tags": ["x"], "format": fmt, "scenes": [{"narration": "n"}]}
        return main.build_metadata(script, episode=1)[1]

    def test_the_writers_hashtags_are_stripped_from_the_body(self):
        d = self._description("design", "A jetliner glided. #history #aviation")
        self.assertNotIn("#aviation", d)
        self.assertIn("A jetliner glided.", d)

    def test_exactly_three_hashtags_are_written(self):
        d = self._description("design", "No tags here.")
        self.assertEqual(d.count("#"), 3)

    def test_the_format_picks_them(self):
        self.assertIn("#engineering", self._description("design", "x"))
        self.assertIn("#mystery", self._description("unsolved", "x"))
        self.assertIn("#militaryhistory", self._description("order", "x"))

    def test_shorts_keeps_its_placement_tag_first(self):
        d = self._description("order", "x").strip()
        self.assertTrue(d.split("\n")[-1].startswith("#shorts"))

    def test_stripping_leaves_ordinary_text_alone(self):
        self.assertEqual(main._strip_hashtags("A sentence, and another."),
                         "A sentence, and another.")
        self.assertEqual(main._strip_hashtags(""), "")


class TestNarrationDoesNotOverlap(unittest.TestCase):
    """Reported from a published Short: "line B comes while A is fading".
    acrossfade was mixing the tail of one sentence into the head of the next.
    The long-form engine was fixed for this; the Shorts engine was not."""

    @staticmethod
    def _code(fn):
        """Source with comments stripped — the narration crossfade is named in
        a comment explaining its removal, and that must not read as its use."""
        import inspect
        return "\n".join(line.split("#")[0]
                         for line in inspect.getsource(fn).split("\n"))

    def test_the_audio_is_summed_not_crossfaded(self):
        code = self._code(video_engine.generate_video_pipeline)
        self.assertNotIn("acrossfade", code)
        self.assertIn("adelay", code)
        self.assertIn("amix", code)

    def test_each_clip_carries_a_silent_tail_to_dissolve_across(self):
        import inspect
        src = inspect.getsource(video_engine.create_scene_clip)
        self.assertIn("apad", src)
        self.assertIn("duration + XFADE", src)

    def test_scene_offsets_are_the_cumulative_narration(self):
        """With clips XFADE longer and overlapping by XFADE, scene i begins
        exactly when scene i-1 stopped speaking — which is also where its
        captions and transcript cues belong."""
        import inspect
        src = inspect.getsource(video_engine.generate_video_pipeline)
        self.assertIn("t += durations[i]", src)
        self.assertNotIn("t += durations[i] - XFADE", src)

    def test_total_duration_accounts_for_the_tail(self):
        import inspect
        src = inspect.getsource(video_engine.generate_video_pipeline)
        self.assertIn("total = sum(durations) + XFADE", src)


class TestVisualQualityGate(unittest.TestCase):
    """On 19 August the day's third run found Cloudflare's free allocation
    spent, rendered all five frames on the fallback, and published anyway."""

    def setUp(self):
        os.environ["CF_ACCOUNT_ID"] = "test"
        os.environ["CF_API_TOKEN"] = "test"
        os.environ.pop("MIN_PREFERRED_FRAMES", None)

    def tearDown(self):
        os.environ.pop("CF_ACCOUNT_ID", None)
        os.environ.pop("CF_API_TOKEN", None)

    def _check(self, sources):
        main.check_visual_quality(
            {"scenes": [{"image_source": s} for s in sources]})

    def test_an_all_fallback_video_is_not_published(self):
        with self.assertRaises(SystemExit):
            self._check(["pollinations/flux"] * 5)

    def test_a_mostly_fallback_video_is_not_published(self):
        with self.assertRaises(SystemExit):
            self._check(["workers-ai"] + ["pollinations/flux"] * 4)

    def test_a_single_rescued_frame_is_fine(self):
        self._check(["workers-ai"] * 4 + ["pollinations/flux"])

    def test_a_clean_run_passes(self):
        self._check(["workers-ai"] * 5)

    def test_the_floor_is_a_share_not_a_count(self):
        """Scripts have four to six scenes; a fixed count would gate them
        differently for no reason."""
        self._check(["workers-ai"] * 3 + ["pollinations/flux"])   # 4 scenes
        self._check(["workers-ai"] * 4 + ["pollinations/flux"] * 2)  # 6 scenes

    def test_no_cloudflare_key_means_no_gate(self):
        """Without credentials Pollinations IS the intended generator, and
        gating would refuse to publish at all."""
        os.environ.pop("CF_ACCOUNT_ID", None)
        os.environ.pop("CF_API_TOKEN", None)
        self._check(["pollinations/flux"] * 5)

    def test_the_gate_can_be_overridden(self):
        import importlib
        os.environ["MIN_PREFERRED_FRAMES"] = "0"
        try:
            importlib.reload(main)
            main.check_visual_quality(
                {"scenes": [{"image_source": "pollinations/flux"}] * 5})
        finally:
            os.environ.pop("MIN_PREFERRED_FRAMES", None)
            importlib.reload(main)

    def test_the_generator_is_recorded_on_every_frame(self):
        import inspect
        src = inspect.getsource(visual_engine.generate_visual_pipeline)
        self.assertIn("image_source", src)


if __name__ == "__main__":
    unittest.main()
