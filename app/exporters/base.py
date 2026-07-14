from typing import Protocol

from app.schemas.lead import Lead


class Exporter(Protocol):
    def export(self, leads: list[Lead]) -> str: ...
