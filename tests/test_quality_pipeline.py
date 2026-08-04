"""
Tests for the visual-quality upgrade: archival sourcing, shot pacing, and the
on-screen typography fixes. No network — everything here is pure logic or
in-memory Pillow.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PIL import Image                                       # noqa: E402

import archive_engine                                       # noqa: E402
import llm_engine                                           # noqa: E402
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
