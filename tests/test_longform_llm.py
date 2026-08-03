"""
Tests for the parts of the long-form pipeline that fail silently in CI.

Two things are covered here, both learned from live failures:

1. The public surface of longform_llm. A refactor once left the module without
   generate_longform_script, and nothing noticed until the weekly workflow died
   on an ImportError six minutes into a scheduled run.
2. _extract_json against the replies free models actually send — fenced, prefaced
   with reasoning, comma-happy, and cut off mid-sentence at the token ceiling.

Stdlib only, no network: `python -m unittest discover -s tests`.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from llm_engine import _extract_json                       # noqa: E402
import longform_llm                                        # noqa: E402


class TestModuleSurface(unittest.TestCase):
    """longform_main imports these by name. If one goes missing the weekly run
    dies before it writes a single line of script."""

    REQUIRED = ("generate_longform_script", "flatten_scenes",
                "select_historical_anchor", "QuotaExhausted")

    def test_longform_main_imports_resolve(self):
        for name in self.REQUIRED:
            self.assertTrue(hasattr(longform_llm, name),
                            f"longform_llm.{name} is missing")

    def test_arc_is_intact(self):
        self.assertEqual(len(longform_llm.ARC), 6)
        for heading, brief, words in longform_llm.ARC:
            self.assertTrue(heading and brief)
            self.assertGreater(words, 0)


class TestExtractJson(unittest.TestCase):

    def test_plain_object(self):
        self.assertEqual(_extract_json('{"a": 1}'), {"a": 1})

    def test_fenced(self):
        self.assertEqual(_extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_reasoning_preamble_then_fenced(self):
        raw = ('Okay, the user wants a chapter about the Vasa. Let me write it.\n\n'
               '```json\n{"segments": [{"narration": "x", "image_prompt": "y"}]}\n```\n'
               'Let me know if you want another pass.')
        self.assertEqual(len(_extract_json(raw)["segments"]), 1)

    def test_commentary_around_bare_object(self):
        raw = 'Here is the chapter:\n{"a": 1}\nHope that helps.'
        self.assertEqual(_extract_json(raw), {"a": 1})

    def test_trailing_commas(self):
        raw = '{"segments": [{"narration": "x", "image_prompt": "y"},],}'
        self.assertEqual(len(_extract_json(raw)["segments"]), 1)

    def test_smart_quotes_as_delimiters(self):
        raw = '{“key_stat”: null, “segments”: []}'
        self.assertEqual(_extract_json(raw), {"key_stat": None, "segments": []})

    def test_truncated_mid_string_keeps_complete_segments(self):
        """The exact shape of the failure that killed the trending run:
        'Unterminated string starting at ...'."""
        raw = ('{"key_stat": {"value": "72", "label": "dead at the border"}, '
               '"segments": ['
               '{"narration": "First segment.", "image_prompt": "wide shot"}, '
               '{"narration": "Second segment.", "image_prompt": "close-up"}, '
               '{"narration": "Third segment, cut off here", "image_prompt": "low-ang')
        data = _extract_json(raw)
        self.assertEqual(len(data["segments"]), 2)
        self.assertEqual(data["key_stat"]["value"], "72")
        self.assertEqual(data["segments"][0]["narration"], "First segment.")

    def test_truncated_after_comma(self):
        raw = ('{"segments": [{"narration": "First.", "image_prompt": "wide"}, ')
        self.assertEqual(len(_extract_json(raw)["segments"]), 1)

    def test_truncated_before_any_complete_segment_still_raises(self):
        raw = '{"segments": [{"narration": "half a sen'
        with self.assertRaises(ValueError):
            _extract_json(raw)

    def test_salvage_never_invents_a_usable_chapter(self):
        """Cut off before the first segment closes, the salvage can only
        recover key_stat. That must fail validation and be retried, not sail
        through as a chapter with no narration in it."""
        raw = ('{"key_stat": {"value": "72", "label": "dead"}, '
               '"segments": [{"narration": "half a sen')
        self.assertFalse(longform_llm._chapter_usable(_extract_json(raw)))

    def test_salvaged_chapter_passes_the_usability_check(self):
        """Repair is only worth anything if the result survives validation."""
        raw = ('{"segments": ['
               '{"narration": "First segment.", "image_prompt": "wide shot"}, '
               '{"narration": "Second.", "image_prompt": "cut off mid pro')
        self.assertTrue(longform_llm._chapter_usable(_extract_json(raw)))

    def test_empty_content_raises(self):
        for raw in ("", "   ", None):
            with self.assertRaises(ValueError):
                _extract_json(raw)

    def test_prose_with_no_json_raises(self):
        with self.assertRaises(ValueError):
            _extract_json("I cannot help with that request.")

    def test_curly_quotes_inside_narration_survive(self):
        """The smart-quote rewrite is a last resort, so a valid reply that uses
        typographic quotes inside its text must not be mangled by it."""
        raw = '{"narration": "The order read “hold position”."}'
        self.assertEqual(_extract_json(raw)["narration"],
                         "The order read “hold position”.")


class TestModelRotation(unittest.TestCase):

    def setUp(self):
        longform_llm._parse_failures.clear()
        longform_llm._preferred_model = None

    def tearDown(self):
        longform_llm._parse_failures.clear()
        longform_llm._preferred_model = None

    def test_repeat_offender_is_dropped(self):
        models = ["good/model", "bad/model"]
        longform_llm._parse_failures["bad/model"] = longform_llm.MAX_PARSE_FAILURES
        self.assertEqual(longform_llm._order_models(models), ["good/model"])

    def test_all_models_failing_falls_back_to_trying_them_anyway(self):
        models = ["a/model", "b/model"]
        for m in models:
            longform_llm._parse_failures[m] = longform_llm.MAX_PARSE_FAILURES
        self.assertEqual(set(longform_llm._order_models(models)), set(models))

    def test_preferred_model_goes_first(self):
        longform_llm._preferred_model = "b/model"
        self.assertEqual(longform_llm._order_models(["a/model", "b/model"])[0],
                         "b/model")


if __name__ == "__main__":
    unittest.main()
