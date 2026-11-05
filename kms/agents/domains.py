"""Domain specialist agents - one LangGraph sub-graph per knowledge system.

Plain English: specialist librarians. The Jira one knows tickets (keys like
PAY-142, statuses, linked issues); the Confluence one knows documentation.
Each one only talks to its own MCP server.

Adding a source (e.g. ServiceNow in Phase 2) = add an entry to DOMAINS.
"""
from __future__ import annotations

import logging
import operator
import re
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from kms.agents.mcp_client import call_tool

log = logging.getLogger("kms.agents.domain")
ISSUE_KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")

DOMAINS: dict[str, dict] = {
    "confluence": {"server": "atlassian", "search": "search_pages", "enabled": True,
                   "describe": "documentation, how-to guides, runbooks, policies, architecture, onboarding"},
    "jira": {"server": "atlassian", "search": "search_issues", "get": "get_issue", "links": "get_linked_issues",
             "enabled": True,
             "describe": "tickets, bugs, incidents, tasks, status of work, who is working on what, past fixes"},
    "servicenow": {"server": "servicenow", "search": "search_kb", "enabled": False,  # Phase 2
                   "describe": "IT incidents, service requests, IT knowledge base"},
}


def enabled_sources() -> list[str]:
    return [k for k, v in DOMAINS.items() if v["enabled"]]


class DomainState(TypedDict, total=False):
    source: str
    query: str
    evidence: Annotated[list, operator.add]
    errors: Annotated[list, operator.add]


def _build(source: str):
    cfg = DOMAINS[source]

    async def search(state: DomainState) -> dict:
        try:
            hits = await call_tool(cfg["server"], cfg["search"], {"query": state["query"], "limit": 6})
        except Exception as e:  # noqa: BLE001 - one failing source must not sink the whole answer
            log.exception("domain %s search failed", source)
            return {"evidence": [], "errors": [f"{source}: {e}"]}
        problems = [h for h in hits or [] if isinstance(h, str)]
        if problems:  # the MCP server reported a tool error
            log.error("domain %s tool error: %s", source, problems[0][:300])
        hits = [h for h in hits or [] if isinstance(h, dict) and "doc_id" in h]
        return {"evidence": hits, "errors": [f"{source}: {p[:200]}" for p in problems]}

    async def expand(state: DomainState) -> dict:
        """Jira only: if the question names a ticket, read it directly; follow links of the top hit."""
        extra = []
        have = {e["doc_id"] for e in state.get("evidence", [])}
        for key in ISSUE_KEY.findall(state["query"])[:3]:
            if f"jira:{key}" in have:
                continue
            try:
                item = await call_tool(cfg["server"], cfg["get"], {"issue_key": key}, single=True)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(item, dict) and "doc_id" in item:
                extra.append({"doc_id": item["doc_id"], "source": "jira", "title": item["title"],
                              "url": item.get("url", ""), "heading": "", "text": item["text"][:2000], "score": 1.0,
                              "classification": item.get("classification"), "updated_at": item.get("updated_at"),
                              "stale": False})
        return {"evidence": extra}

    g = StateGraph(DomainState)
    g.add_node("search", search)
    g.add_edge(START, "search")
    if source == "jira":
        g.add_node("expand", expand)
        g.add_edge("search", "expand")
        g.add_edge("expand", END)
    else:
        g.add_edge("search", END)
    return g.compile(name=f"{source}_agent")


SUBGRAPHS = {name: _build(name) for name in DOMAINS}
