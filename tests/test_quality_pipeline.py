"""
Tests for the visual-quality upgrade: archival sourcing, shot pacing, and the
on-screen typography fixes. No network — everything here is pure logic or
in-memory Pillow.
"""

import base64
import io
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PIL import Image                                       # noqa: E402

import archive_engine                                       # noqa: E402
import audio_engine                                         # noqa: E402
import llm_engine                                           # noqa: E402
import llm_provider                                         # noqa: E402
import longform_llm                                         # noqa: E402
import longform_video                                       # noqa: E402
import longform_visual                                      # noqa: E402
import visual_engine                                        # noqa: E402


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
        event = longform_video._chapter_title_event(0.3, 12.0, 1, "Cold Open")
        self.assertIn("PART ONE", event)
        self.assertIn("COLD OPEN", event)
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

    def setUp(self):
        self._saved = os.environ.pop("GEMINI_API_KEY", None)

    def tearDown(self):
        if self._saved:
            os.environ["GEMINI_API_KEY"] = self._saved

    def test_defaults_to_openrouter_without_a_key(self):
        self.assertFalse(llm_provider.gemini_available())
        self.assertIn("OpenRouter", llm_provider.describe_provider())

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


if __name__ == "__main__":
    unittest.main()
