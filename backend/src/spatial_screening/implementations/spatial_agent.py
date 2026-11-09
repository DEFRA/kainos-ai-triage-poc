import json
from pathlib import Path
from typing import Union

from backend.src.utilities.llm_client import LLMClient
from ..tools.internal_irz_client import InternalIRZClient
from ..tools.utilities import check_strategic, get_consultation_questions


_ANSWER_IRZ_QUESTION_PROMPT = (
	Path(__file__).parent.parent / "prompts" / "answer_irz_question.md"
)


def run_spatial_screening(
	polygon: list[list[float]],
	context: dict[str, object],
	model_config_path: Union[str, Path],
) -> dict:
	"""Answer IRZ questions for a BNG polygon ring of [easting, northing] points."""
	irz_client = InternalIRZClient()
	internal_link = irz_client.get_internal_link(polygon)
	is_strategic_consultation = bool(check_strategic(internal_link))
	questions = get_consultation_questions(internal_link)

	answered_questions = []
	if questions:
		system_prompt = _ANSWER_IRZ_QUESTION_PROMPT.read_text(encoding="utf-8")
		with LLMClient(model_config_path) as intelligence:
			for question in questions:
				prompt = (
					"IRZ question and available answer options:\n"
					f"{json.dumps(question, ensure_ascii=False)}\n\n"
					"Additional consultation context:\n"
					f"{json.dumps(context, ensure_ascii=False, indent=2)}"
				)
				answered_questions.append({
					"question": question,
					"answer": intelligence.generate(
						prompt,
						system_prompt=system_prompt,
					),
				})

	return {
		"polygon": polygon,
		"is_strategic_consultation": is_strategic_consultation,
		"answered_questions": answered_questions,
	}