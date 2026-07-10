from app.tools.base import ToolRegistry


class _Echo:
    name = "echo"
    description = "echoes text"

    def run(self, **kwargs):
        return f"echo: {kwargs.get('text', '')}"


def test_describe_lists_tools():
    reg = ToolRegistry([_Echo()])
    text = reg.describe()
    assert "echo" in text
    assert "echoes text" in text


def test_run_dispatches_to_tool():
    reg = ToolRegistry([_Echo()])
    assert reg.run("echo", {"text": "hi"}) == "echo: hi"


def test_run_unknown_tool_returns_error_string():
    reg = ToolRegistry([_Echo()])
    out = reg.run("missing", {})
    assert "unknown tool" in out.lower()
