from typing import Protocol

from pydantic import BaseModel


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str


class SearchBackend(Protocol):
    def search(self, query: str, k: int = 5) -> list[SearchResult]: ...
