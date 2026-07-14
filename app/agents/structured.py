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
    max_retries: int = 6,
) -> BaseModel:
    last_response = None
    last_reason = "no response received"

    for _ in range(max_retries):
        resp = llm.complete(messages)
        last_response = resp
        messages.append(ChatMessage(role="assistant", content=resp.content))
        parsed = extract_json_object(resp.content)

        if parsed is None:
            last_reason = "response did not contain a valid JSON object"
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
            last_reason = f"validation error: {exc}"
            messages.append(
                ChatMessage(role="user", content=f"That was invalid ({exc}). Fix and resend.")
            )
            continue

    finish_reason = last_response.finish_reason if last_response else None
    raw = last_response.content if last_response else ""
    raise StructuredOutputError(
        f"Could not get a valid {schema.__name__} within {max_retries} attempts. "
        f"Last failure: {last_reason}. Last finish_reason: {finish_reason!r}. "
        f"Last raw response ({len(raw)} chars): {raw!r}"
    )
