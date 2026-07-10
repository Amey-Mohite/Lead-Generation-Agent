from datetime import datetime, timezone

from app.tools.current_date import CurrentDateTool


def test_returns_formatted_date_from_injected_clock():
    fixed = datetime(2026, 7, 10, 15, 30, tzinfo=timezone.utc)
    tool = CurrentDateTool(now_fn=lambda: fixed)

    assert tool.run() == "2026-07-10"
    assert tool.name == "current_date"


def test_default_clock_returns_a_real_date_string():
    tool = CurrentDateTool()
    result = tool.run()

    # loosely validate shape without asserting an exact date (would be flaky otherwise)
    assert len(result) == 10
    assert result[4] == "-" and result[7] == "-"
