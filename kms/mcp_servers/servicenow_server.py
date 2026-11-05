"""ServiceNow MCP server - TEMPLATE for Phase 2 (not deployed in the pilot).

Same find-then-confirm pattern as the Atlassian server:
  FIND    - Qdrant collection "kms_servicenow" with roles + KB user criteria as ACL payload
  CONFIRM - re-read the record via the Table / Knowledge API as the user, so
            ServiceNow's script-based ACLs make the final decision.
To finish it in Phase 2: implement `ServiceNow` in kms/sources/servicenow.py,
add an ingestion handler, and register the "servicenow" domain agent
(kms/agents/domains.py) - no orchestrator changes are needed.
"""
from __future__ import annotations

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

mcp = FastMCP("kms-servicenow", host="0.0.0.0", port=8000, stateless_http=True, json_response=True,
              transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

PHASE2 = {"error": "ServiceNow connector is scheduled for Phase 2"}


@mcp.tool()
async def search_incidents(query: str, ctx: Context, limit: int = 6) -> list[dict]:
    """Search incidents and problems the caller may read (Phase 2)."""
    return [PHASE2]


@mcp.tool()
async def search_kb(query: str, ctx: Context, limit: int = 6) -> list[dict]:
    """Search knowledge-base articles the caller may read (Phase 2)."""
    return [PHASE2]


@mcp.tool()
async def get_record(table: str, sys_id: str, ctx: Context) -> dict:
    """Read one record live, as the caller (Phase 2)."""
    return PHASE2


app = mcp.streamable_http_app()
