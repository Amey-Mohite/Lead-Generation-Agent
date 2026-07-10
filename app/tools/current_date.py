from collections.abc import Callable
from datetime import datetime, timezone


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CurrentDateTool:
    """Reports today's date (UTC) -- useful for judging how recent a fact or source is."""

    name = "current_date"
    description = "Get today's date in UTC (YYYY-MM-DD). Takes no arguments -- call with {}."

    def __init__(self, now_fn: Callable[[], datetime] | None = None) -> None:
        self._now_fn = now_fn or _utc_now

    def run(self) -> str:
        return self._now_fn().strftime("%Y-%m-%d")
