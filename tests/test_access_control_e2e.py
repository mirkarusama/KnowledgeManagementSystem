"""Persona-based access-control suite (target: 100% pass, zero tolerance for leaks).

Runs against a live stack (mock Atlassian, MCP server, index loaded with `kms index-all`):
    KMS_E2E=1 pytest tests/test_access_control_e2e.py
Every persona asks broad questions; every returned document must be on that persona's allowed list.
"""
import os

import httpx
import pytest

from kms.agents.context import current_user
from kms.agents.mcp_client import call_tool
from kms.auth import debug_user
from kms.mock_atlassian.personas import debug_header

pytestmark = pytest.mark.skipif(os.getenv("KMS_E2E") != "1", reason="needs the running stack (KMS_E2E=1)")

ALLOWED = {
    "alice": {"confluence:1001", "confluence:1002", "confluence:1003", "confluence:1004", "confluence:1005",
              "confluence:1006", "confluence:3001", "confluence:3002",
              "jira:PAY-142", "jira:PAY-150", "jira:OPS-12", "jira:OPS-15"},
    "bob": {"confluence:1001", "confluence:1002", "confluence:1006", "confluence:3001", "confluence:3002",
            "jira:PAY-142", "jira:PAY-150", "jira:OPS-12", "jira:OPS-15"},
    "carol": {"confluence:2001", "confluence:3001", "confluence:3002", "jira:HR-7"},
    "dave": {"confluence:1001", "confluence:1002", "confluence:1005", "confluence:1006", "confluence:3001",
             "confluence:3002", "jira:PAY-142", "jira:PAY-150", "jira:PAY-160", "jira:OPS-12", "jira:OPS-15"},
    "erin": {"confluence:3001", "confluence:3002", "jira:OPS-12", "jira:OPS-15"},
}
QUERIES = ["database access", "payments deploy", "parental leave", "salary bands", "VPN outage", "card numbers logs",
           "on-call", "architecture", "expenses", "break-glass", "lunch", "Jenkins build", "policy", "runbook",
           "Stripe SDK upgrade", "failover"]
MOCK = os.getenv("KMS_JIRA_URL", "http://localhost:9000")


async def _seen(persona: str) -> set[str]:
    current_user.set(debug_user(debug_header(persona)))
    seen = set()
    for q in QUERIES:
        for tool in ("search_pages", "search_issues"):
            for hit in await call_tool("atlassian", tool, {"query": q, "limit": 10}):
                seen.add(hit["doc_id"])
    return seen


@pytest.mark.parametrize("persona", sorted(ALLOWED))
async def test_persona_sees_only_allowed(persona):
    seen = await _seen(persona)
    leaked = seen - ALLOWED[persona]
    assert not leaked, f"{persona} saw forbidden documents: {leaked}"
    assert seen, f"{persona} saw nothing at all - is the index loaded?"


async def test_prompt_injection_page_is_never_returned():
    assert "confluence:1007" not in await _seen("alice")


async def test_revocation_is_enforced_before_next_sync():
    """Remove alice from 'sre' in the source only. Index + token still say sre; the live confirm must deny."""
    async with httpx.AsyncClient() as c:
        await c.post(f"{MOCK}/admin/members/alice", json={"groups": ["all-staff", "engineering"]})
    try:
        seen = await _seen("alice")
        assert "confluence:1003" not in seen and "confluence:1004" not in seen
    finally:
        async with httpx.AsyncClient() as c:
            await c.post(f"{MOCK}/admin/members/alice", json={"groups": ["all-staff", "engineering", "sre"]})


async def test_direct_read_of_forbidden_issue_is_denied():
    current_user.set(debug_user(debug_header("bob")))
    res = await call_tool("atlassian", "get_issue", {"issue_key": "PAY-160"}, single=True)
    assert "error" in res
