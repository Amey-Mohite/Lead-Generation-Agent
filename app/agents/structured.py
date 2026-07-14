from pydantic import BaseModel, ValidationError

from app.agents.json_utils import extract_json_object
from app.providers.llm.base import ChatMessage, LLMProvider


class StructuredOutputError(Exception):
    pass


def complete_structured(
    llm: LLMProvider,
    messages: list[ChatMessage],
    schema: type[BaseModel],
    *,
    max_retries: int = 3,
) -> BaseModel:
    for _ in range(max_retries):
        resp = llm.complete(messages)
        messages.append(ChatMessage(role="assistant", content=resp.content))
        parsed = extract_json_object(resp.content)

        if parsed is None:
            messages.append(
                ChatMessage(
                    role="user",
                    content="That was not valid JSON. Reply with ONE JSON object only.",
                )
            )
            continue

        try:
            return schema(**parsed)
        except ValidationError as exc:
            messages.append(
                ChatMessage(role="user", content=f"That was invalid ({exc}). Fix and resend.")
            )
            continue

    raise StructuredOutputError(
        f"Could not get a valid {schema.__name__} within {max_retries} attempts."
    )
