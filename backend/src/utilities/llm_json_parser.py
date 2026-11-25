import json
import re
import json_repair # https://pypi.org/project/json-repair/


def parse_llm_json(response: str):
	"""Return the decoded JSON value, repairing malformed LLM output if needed.

	Tries standard JSON parsing before falling back to json-repair. 
	Raises TypeError for non-text input and ValueError if repair finds no value.
	Assumes a single JSON value. Repair can recover formatting errors but may
		guess missing content: for example, '{"a":' can become {"a": ""}.
	The result is not schema-validated, and validation cannot guarantee accuracy.
	"""
	if not isinstance(response, str):
		raise TypeError("The LLM response must be text.")

	# Remove surrounding whitespace and any leading Unicode byte-order mark.
	content = response.strip().lstrip("\ufeff").strip()
	# Unwrap a complete Markdown fence so valid JSON, including "", parses directly.
	fence = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*?)\r?\n```", content, re.IGNORECASE | re.DOTALL)
	if fence:
		content = fence.group(1).strip()
	try:
		return json.loads(content)
	except json.JSONDecodeError:
		pass

	result = json_repair.loads(content)
	if result == "":
		raise ValueError("LLM response is not valid JSON.")
	return result
