"""Tiny MCP server used by tests: one read-only tool, one tool that changes things."""
try:
    from mcp.server.mcpserver import MCPServer as Server          # SDK 2.x
except ImportError:
    from mcp.server.fastmcp import FastMCP as Server              # SDK 1.x
from mcp.types import ToolAnnotations

app = Server("demo")


@app.tool(annotations=ToolAnnotations(readOnlyHint=True))
def add(a: int, b: int) -> str:
    """Add two numbers."""
    return str(a + b)


@app.tool()
def wipe_calendar(day: str) -> str:
    """Delete every event on a day (pretend)."""
    return f"wiped {day}"


@app.tool()
def fail() -> str:
    """Always fails."""
    raise RuntimeError("boom")


if __name__ == "__main__":
    app.run("stdio")
