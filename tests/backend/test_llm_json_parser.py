import json
import unittest
from backend.src.utilities.llm_json_parser import parse_llm_json


class LlmJsonParserTests(unittest.TestCase):
    def test_preserves_valid_json_values(self):
        """Valid JSON should decode without changing its value.

        Example: the JSON text '[1, null]' becomes the Python list [1, None].
        """
        for value in ({"text": "comma, } and \"quoted\""}, [1, None], "text", 42, False, None):
            with self.subTest(value=value):
                self.assertEqual(parse_llm_json(json.dumps(value)), value)

    def test_repairs_fences_bom_and_trailing_commas(self):
        """Remove formatting artifacts and repair trailing commas.

        Example: fenced JSON with a leading byte-order mark and '[1, 2,]'
        is decoded as [1, 2]. The full response also contains an object.
        """
        response = '\ufeff```JSON\n{"items": [1, 2,], "text": "comma, }",}\n```'
        self.assertEqual(parse_llm_json(response), {"items": [1, 2], "text": "comma, }"})

    def test_preserves_fenced_empty_string(self):
        """Treat an empty JSON string inside a code fence as a valid value.

        Example: a fenced '""' becomes the Python empty string '', not an error.
        """
        for response in ('```json\n""\n```', '```\r\n""\r\n```'):
            with self.subTest(response=response):
                self.assertEqual(parse_llm_json(response), "")

    def test_preserves_escaped_quotes_during_repair(self):
        """Keep escaped quotation marks in text while fixing a trailing comma.

        Example: the value 'escaped " quote, ]' stays exactly the same.
        """
        response = json.dumps({"text": 'escaped " quote, ]'})[:-1] + ",}"
        self.assertEqual(parse_llm_json(response), {"text": 'escaped " quote, ]'})

    def test_extracts_json_from_surrounding_text(self):
        """Extract the JSON when the LLM adds explanatory text around it.

        Example: 'Here is JSON: {"a": 1} Done.' becomes the dict {"a": 1}.
        The test also covers explanatory text around a Markdown code fence.
        """
        cases = (
            ('Here is JSON: {"a": 1} Done.', {"a": 1}),
            ('Result:\n```json\n{"a": [1, 2,],}\n```\nDone.', {"a": [1, 2]}),
        )
        for response, expected in cases:
            with self.subTest(response=response):
                self.assertEqual(parse_llm_json(response), expected)

    def test_repairs_missing_closing_braces(self):
        """Add a missing closing brace to an incomplete JSON object.

        Example: '{"outer": {"inner": 1}' becomes {"outer": {"inner": 1}}.
        """
        self.assertEqual(parse_llm_json('{"outer": {"inner": 1}'), {"outer": {"inner": 1}})

    def test_rejects_responses_without_json(self):
        """Raise ValueError when no JSON value can be found.

        Example: both '' and 'No JSON here' are rejected.
        """
        for response in ('', 'No JSON here'):
            with self.subTest(response=response):
                with self.assertRaisesRegex(ValueError, "not valid JSON"):
                    parse_llm_json(response)

    def test_rejects_non_text(self):
        """Raise TypeError when the response is not a string.

        Example: passing None raises TypeError instead of attempting to parse it.
        """
        with self.assertRaises(TypeError):
            parse_llm_json(None)


if __name__ == "__main__":
    unittest.main()