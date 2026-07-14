from pydantic import ValidationError

from app.agents.json_utils import extract_json_object
from app.providers.llm.base import ChatMessage, LLMProvider
from app.schemas.research import ResearchBrief
from app.tools.base import ToolRegistry


class ResearchError(Exception):
    pass


_SYSTEM = """You are a research agent. Research the company target the user gives you and \
produce a structured brief.

You can use these tools:
{tools}

On EACH turn respond with ONE JSON object and nothing else, in one of two forms:
1. Use a tool:   {{"action": {{"tool": "<name>", "args": {{...}}}}}}
2. Finish:       {{"final": {{"company_name": "...", "domain": "...", "industry": "...", \
"summary": "...", "key_facts": ["..."], "contacts": [{{"name": "...", "role": "...", \
"email": "..."}}], "sources": ["..."]}}}}

Rules:
- Only ONE action per turn. Base every fact on tool observations; do not invent.
- "company_name" and "summary" are required in the final brief.
- "domain" must be a bare hostname only (e.g. "acme.com"), never a sentence, explanation, or
  markdown link. If the target's domain doesn't resolve but you find the company's real domain
  during research, use that real bare hostname instead. If no working domain can be confirmed,
  omit "domain" entirely rather than describing the situation in prose.
- Finish within {max_steps} steps."""


class ResearchAgent:
    def __init__(self, llm: LLMProvider, registry: ToolRegistry, max_steps: int = 6) -> None:
        self._llm = llm
        self._registry = registry
        self._max_steps = max_steps

    def run(self, target: str) -> ResearchBrief:
        system = _SYSTEM.format(tools=self._registry.describe(), max_steps=self._max_steps)
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=f"Research this company: {target}"),
        ]
        print(messages)

        for _ in range(self._max_steps):
            resp = self._llm.complete(messages)
            print(f"LLM response: {resp.content}")  # truncate for readability
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

            if "final" in parsed:
                try:
                    return ResearchBrief(**parsed["final"])
                except ValidationError as exc:
                    messages.append(
                        ChatMessage(
                            role="user",
                            content=f"The final brief was invalid ({exc}). Fix and resend.",
                        )
                    )
                    continue
            print(f"Parsed: {parsed}")
            action = parsed.get("action") or {}
            observation = self._registry.run(action.get("tool", ""), action.get("args", {}))
            print(f"Observation: {observation}")  # truncate for readability
            messages.append(ChatMessage(role="user", content=f"Observation:\n{observation}"))

        raise ResearchError(f"Research did not finish within {self._max_steps} steps.")


def build_research_agent(settings) -> "ResearchAgent":
    from app.providers.llm.factory import build_llm_provider
    from app.providers.llm.fallback import FallbackLLM
    from app.providers.llm.online import OnlineSearchLLM
    from app.tools.fetch_url import FetchUrlTool
    from app.tools.search.factory import build_search_backend
    from app.tools.web_search import WebSearchTool

    base_llm = build_llm_provider(settings)

    if settings.research_search_mode == "native":
        # The model does its own web search internally (e.g. OpenRouter ":online"
        # models) -- no external web_search tool call is needed for that.
        llm = OnlineSearchLLM(base_llm)
        tools = [FetchUrlTool()]
    else:
        llm = base_llm
        tools = [WebSearchTool(build_search_backend(settings)), FetchUrlTool()]

    llm = FallbackLLM(llm, settings.llm_fallback_model)
    registry = ToolRegistry(tools)
    return ResearchAgent(llm, registry)
