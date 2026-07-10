from app.config import Settings
from app.tools.search.base import SearchBackend
from app.tools.search.mock import MockSearchBackend
from app.tools.search.tavily import TavilySearchBackend


def build_search_backend(settings: Settings) -> SearchBackend:
    if settings.research_search_mode == "api" and settings.search_provider == "tavily":
        return TavilySearchBackend(api_key=settings.search_api_key)
    # mock | native | anything else -> offline mock (native TODO: web-search model)
    return MockSearchBackend()
