"""Calls MCP tools through langchain-mcp-adapters, forwarding the user's identity."""
from __future__ import annotations

import json

from langchain_mcp_adapters.client import MultiServerMCPClient

from kms.agents.context import current_request_id, current_user
from kms.config import get_settings


def _servers() -> dict[str, str]:
    s = get_settings()
    out = {"atlassian": s.mcp_atlassian_url}
    if s.mcp_servicenow_url:
        out["servicenow"] = s.mcp_servicenow_url
    return out


def _parse(result):
    """Normalise whatever the adapter returns (str / content blocks / dict) into Python data."""
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, (dict,)):
        return result.get("result", result) if set(result) == {"result"} else result
    if isinstance(result, str):
        try:
            data = json.loads(result)
        except json.JSONDecodeError:
            return result
        return data["result"] if isinstance(data, dict) and set(data) == {"result"} else data
    if isinstance(result, list):
        items = []
        for block in result:
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", block)
            parsed = _parse(text) if isinstance(text, str) else text
            if isinstance(parsed, list):
                items.extend(parsed)
            else:
                items.append(parsed)
        return items
    return result


async def call_tool(server: str, tool: str, args: dict, single: bool = False):
    """single=True for tools that return one object (get_issue, get_page)."""
    user = current_user.get()
    headers = {"Authorization": f"Bearer {user.token}", "X-Request-Id": current_request_id.get()}
    client = MultiServerMCPClient({server: {"url": _servers()[server], "transport": "streamable_http",
                                            "headers": headers}})
    tools = {t.name: t for t in await client.get_tools(server_name=server)}
    if tool not in tools:
        raise RuntimeError(f"MCP server '{server}' has no tool '{tool}'")
    result = _parse(await tools[tool].ainvoke(args))
    if single and isinstance(result, list):
        return result[0] if result else {}
    return result
