"""
MCP client: any Model Context Protocol server becomes a set of E.V.A. tools.

Configure servers in config/settings.yaml under `mcp.servers`:

    - name: files
      transport: stdio                       # or "http" with url: http://localhost:8000/mcp
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "D:/Project E.V.A/eva/data/vault"]
      tools: ["read_file", "list_directory"] # allowlist; omit for all (capped at max_tools)
      trusted_tools: []                      # non-read-only tools that may run without asking
      read_only_tools: []                    # treat as read-only if the server doesn't annotate them
      enabled: true

Design (pattern adapted from im4peace/Jarvis mcp_client.py, MIT):
  - One long-lived task per server owns its transport and ClientSession. The SDK's
    cancel scopes must be entered and exited in the same task, so calls from turns
    go through the session while the owner task keeps it open.
  - Tool names are prefixed: server "files" + tool "read_file" -> mcp_files_read_file.
  - Safety: tools annotated readOnlyHint=true run freely. Anything else asks the user
    first (confirmation gate) unless listed in trusted_tools. Non-read-only tools also
    count as actions, so one success ends the tool loop.
  - Best effort: a server that fails to start is logged and skipped. Startup never
    blocks on MCP, and E.V.A. works without the `mcp` package installed.
  - Works with the MCP Python SDK 1.x and 2.x (models are read via model_dump(by_alias=True)).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger

_NAME_OK = re.compile(r"[^a-z0-9_-]+")
MAX_RESULT_CHARS = 4000


def tool_id(server: str, tool: str) -> str:
    return _NAME_OK.sub("_", f"mcp_{server}_{tool}".lower())[:64]


@dataclass
class MCPServerConfig:
    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None
    url: str = ""
    tools: Optional[list[str]] = None
    trusted_tools: list[str] = field(default_factory=list)
    read_only_tools: list[str] = field(default_factory=list)   # for servers that don't annotate reads
    enabled: bool = True
    timeout: float = 30.0
    max_tools: int = 12

    @classmethod
    def from_dict(cls, d: dict) -> "MCPServerConfig":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def _dump(obj: Any) -> dict:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(by_alias=True, exclude_none=True)
    return dict(obj) if isinstance(obj, dict) else {}


def result_to_tool_output(server: str, tool: str, result: Any) -> dict:
    """CallToolResult (either SDK) -> E.V.A.'s {"result": json} shape."""
    d = _dump(result)
    texts = [c.get("text", "") for c in d.get("content", []) if c.get("type") == "text"]
    text = "\n".join(t for t in texts if t).strip()
    if d.get("isError"):
        return {"result": json.dumps({"error": text or f"{tool} failed on {server}"})}
    payload = d.get("structuredContent") or text or "(no output)"
    out = json.dumps({"server": server, "tool": tool, "output": payload}, default=str)
    if len(out) > MAX_RESULT_CHARS:
        out = out[:MAX_RESULT_CHARS] + '..."}'
    return {"result": out}


class MCPServer:
    def __init__(self, cfg: MCPServerConfig):
        self.cfg = cfg
        self.session = None
        self.tools: list[dict] = []
        self.error: Optional[str] = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    @property
    def connected(self) -> bool:
        return self.session is not None and self.error is None

    def _transport(self):
        if self.cfg.transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client
            env = {**os.environ, **{k: str(v) for k, v in self.cfg.env.items()}}
            return stdio_client(StdioServerParameters(command=self.cfg.command, args=list(self.cfg.args),
                                                      env=env, cwd=self.cfg.cwd))
        if self.cfg.transport == "http":
            try:
                from mcp.client.streamable_http import streamable_http_client as http_client   # SDK 2.x
            except ImportError:
                from mcp.client.streamable_http import streamablehttp_client as http_client    # SDK 1.x
            return http_client(self.cfg.url)
        raise ValueError(f"unknown transport {self.cfg.transport!r}")

    async def _owner(self) -> None:
        try:
            from mcp import ClientSession
            async with self._transport() as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    await asyncio.wait_for(session.initialize(), self.cfg.timeout)
                    listed = await asyncio.wait_for(session.list_tools(), self.cfg.timeout)
                    tools = [_dump(t) for t in listed.tools]
                    if self.cfg.tools is not None:
                        tools = [t for t in tools if t["name"] in set(self.cfg.tools)]
                    if len(tools) > self.cfg.max_tools:
                        logger.warning(f"MCP {self.cfg.name}: {len(tools)} tools, keeping the first "
                                       f"{self.cfg.max_tools}. Add a `tools:` allowlist to choose.")
                        tools = tools[: self.cfg.max_tools]
                    self.tools, self.session = tools, session
                    self._ready.set()
                    logger.info(f"MCP {self.cfg.name}: connected, {len(tools)} tools "
                                f"({', '.join(t['name'] for t in tools)})")
                    await self._stop.wait()
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            logger.warning(f"MCP {self.cfg.name}: not available ({self.error})")
        finally:
            self.session = None
            self._ready.set()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._owner(), name=f"mcp-{self.cfg.name}")
        try:
            await asyncio.wait_for(self._ready.wait(), self.cfg.timeout + 5)
        except asyncio.TimeoutError:
            self.error = "timed out starting"
            logger.warning(f"MCP {self.cfg.name}: timed out starting")
            self._stop.set()

    async def call(self, tool: str, args: dict) -> dict:
        if not self.connected:
            return {"result": json.dumps({"error": f"MCP server {self.cfg.name} is not connected"})}
        try:
            res = await asyncio.wait_for(self.session.call_tool(tool, args or {}), self.cfg.timeout)
        except Exception as e:
            return {"result": json.dumps({"error": f"{self.cfg.name}.{tool} failed: {e}"})}
        return result_to_tool_output(self.cfg.name, tool, res)

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, 10)
            except (asyncio.TimeoutError, Exception):
                self._task.cancel()


class MCPManager:
    def __init__(self):
        self.servers: dict[str, MCPServer] = {}
        self._route: dict[str, tuple[MCPServer, dict]] = {}

    async def start(self, configs: list[dict | MCPServerConfig]) -> None:
        try:
            import mcp  # noqa: F401
        except ImportError:
            if configs:
                logger.warning("MCP: servers configured but the `mcp` package is not installed (pip install mcp)")
            return
        for c in configs:
            cfg = c if isinstance(c, MCPServerConfig) else MCPServerConfig.from_dict(c)
            if not cfg.enabled or cfg.name in self.servers:
                continue
            srv = MCPServer(cfg)
            await srv.start()
            self.servers[cfg.name] = srv
            for t in srv.tools:
                self._route[tool_id(cfg.name, t["name"])] = (srv, t)

    def tool_schemas(self) -> list[dict]:
        out = []
        for tid, (srv, t) in self._route.items():
            if not srv.connected:
                continue
            schema = t.get("inputSchema") or {"type": "object", "properties": {}}
            desc = f"[{srv.cfg.name}] {(t.get('description') or t['name']).strip()}"[:300]
            out.append({"type": "function", "function": {"name": tid, "description": desc, "parameters": schema}})
        return out

    def _annotations(self, tid: str) -> dict:
        return self._route[tid][1].get("annotations", {}) if tid in self._route else {}

    def is_read_only(self, tid: str) -> bool:
        if tid not in self._route:
            return False
        srv, t = self._route[tid]
        return bool(self._annotations(tid).get("readOnlyHint")) or t["name"] in set(srv.cfg.read_only_tools)

    def needs_confirm(self, tid: str) -> bool:
        if tid not in self._route or self.is_read_only(tid):
            return False
        srv, t = self._route[tid]
        return t["name"] not in set(srv.cfg.trusted_tools)

    def actions(self) -> set[str]:
        return {tid for tid in self._route if not self.is_read_only(tid)}

    def describe(self, tid: str, args: dict) -> str:
        srv, t = self._route[tid]
        shown = ", ".join(f"{k}={str(v)[:40]}" for k, v in (args or {}).items())
        return f"run {t['name']} on {srv.cfg.name}" + (f" with {shown}" if shown else "")

    def has(self, tid: str) -> bool:
        return tid in self._route and self._route[tid][0].connected

    async def call(self, tid: str, args: dict) -> dict:
        srv, t = self._route[tid]
        return await srv.call(t["name"], args)

    def status(self) -> list[dict]:
        return [{"name": n, "connected": s.connected, "error": s.error,
                 "tools": [tool_id(n, t["name"]) for t in s.tools]} for n, s in self.servers.items()]

    async def stop(self) -> None:
        for s in list(self.servers.values()):
            await s.stop()
        self.servers.clear()
        self._route.clear()


_manager: MCPManager | None = None


def get_mcp() -> MCPManager:
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager
