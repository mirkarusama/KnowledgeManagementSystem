"""Atlassian MCP server (Confluence + Jira), read-only, Streamable HTTP.

Every search tool follows FIND, then CONFIRM:
  FIND    - search the Qdrant index with a filter built from the caller's
            groups; forbidden chunks are never returned.
  CONFIRM - re-read each hit live from Confluence/Jira *as the caller*.
            If the source says no (403/404), the hit is dropped.
  POLICY  - OPA checks the classification label of every surviving hit.
Each step is written to the audit log.

The caller's identity arrives as the HTTP Authorization header on every MCP
call (the agent forwards the user's Keycloak token).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from kms import audit
from kms.auth import AuthError, User, exchange_token, user_from_authorization
from kms.config import get_settings
from kms.guardrails import check_retrieved
from kms.index.chunking import html_sections
from kms.index.qdrant_store import Store
from kms.llm import Embedder
from kms.policy import PolicyClient
from kms.sources.atlassian import Confluence, Jira, SourceDenied, classification_from_labels

log = logging.getLogger("kms.mcp.atlassian")

mcp = FastMCP(
    "kms-atlassian",
    instructions="Read-only Confluence and Jira tools. Results are permission-checked for the calling user.",
    host="0.0.0.0",
    port=8000,
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

_store: Store | None = None
_embedder: Embedder | None = None
_policy = PolicyClient()
_conf = Confluence()
_jira = Jira()


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder


def _caller(ctx: Context) -> tuple[User, str]:
    req = ctx.request_context.request
    headers = req.headers if req is not None else {}
    try:
        user = user_from_authorization(headers.get("authorization"), headers.get("x-debug-user"), audience="kms-mcp")
    except AuthError as e:
        raise PermissionError(str(e)) from e
    return user, headers.get("x-request-id", "")


async def _source_token(user: User) -> str:
    return await exchange_token(user.token, get_settings().source_token_audience)


async def _confirm(source: str, source_id: str, user: User) -> dict | None:
    """Live read as the user. Returns the fresh item, or None if the source denies access."""
    s = get_settings()
    client = _conf if source == "confluence" else _jira
    try:
        if s.confirm_mode == "permission_check":
            if not user.atlassian_id or not await client.user_can_read(source_id, user.atlassian_id):
                return None
            return await client.fetch(source_id)
        return await client.fetch(source_id, user_token=await _source_token(user))
    except SourceDenied:
        return None


def _fresh_text(source: str, item: dict) -> str:
    if source == "confluence":
        html = (item.get("body") or {}).get("storage", {}).get("value", "")
        return "\n".join(f"{sec.heading}\n{sec.text}".strip() for sec in html_sections(html))
    return Jira.issue_text(item)


async def _find_then_confirm(source: str, query: str, ctx: Context, limit: int) -> list[dict]:
    user, request_id = _caller(ctx)
    s = get_settings()
    t0 = time.time()
    labels = await _policy.allowed_labels(user)
    vec = (await embedder().embed([query]))[0]
    hits = await store().search(source, query, vec, user.principals, labels, k=max(limit, s.top_k))
    events: list[dict] = []

    # Confirm each distinct document once, in parallel.
    doc_ids = list(dict.fromkeys(h["doc_id"] for h in hits))
    by_doc = {h["doc_id"]: h for h in hits}
    confirmed = await asyncio.gather(*[_confirm(source, by_doc[d]["source_id"], user) for d in doc_ids])
    live = {d: item for d, item in zip(doc_ids, confirmed)}
    for d, item in live.items():
        events.append(dict(event_type="confirm", request_id=request_id, user=user, source=source, doc_id=d,
                           classification=by_doc[d].get("classification"),
                           decision="allow" if item is not None else "deny",
                           reason=None if item is not None else "source_denied_live_read",
                           details={"query": query}))

    survivors = [h for h in hits if live.get(h["doc_id"]) is not None]
    decisions = await _policy.decide(user, [{"id": h["doc_id"], "classification": h.get("classification"),
                                             "source": source} for h in survivors])
    allowed = {d["id"] for d in decisions if d.get("allow")}
    for d in decisions:
        events.append(dict(event_type="policy_decision", request_id=request_id, user=user, source=source,
                           doc_id=d["id"], decision="allow" if d.get("allow") else "deny", reason=d.get("reason")))

    cutoff = datetime.now(timezone.utc) - timedelta(days=s.freshness_days)
    results = []
    for h in survivors:
        if h["doc_id"] not in allowed:
            continue
        g = check_retrieved(h["text"])
        if not g.allowed:
            events.append(dict(event_type="denial", request_id=request_id, user=user, source=source,
                               doc_id=h["doc_id"], decision="deny", reason=g.reason))
            continue
        item = live[h["doc_id"]]
        live_updated = (item.get("version") or {}).get("createdAt") if source == "confluence" else item["fields"].get("updated")
        updated = live_updated or h.get("updated_at")
        try:
            stale = datetime.fromisoformat(str(updated).replace("Z", "+00:00")) < cutoff
        except ValueError:
            stale = False
        results.append({
            "doc_id": h["doc_id"], "source": source, "title": h["title"], "url": h["url"],
            "heading": h.get("heading", ""), "text": h["text"], "score": round(h.get("score", 0.0), 4),
            "classification": h.get("classification"), "updated_at": updated, "stale": stale,
            "changed_since_indexed": bool(live_updated and live_updated != h.get("updated_at")),
            "space": h.get("space"), "project": h.get("project"), "status": h.get("status"),
            "links": h.get("links") or [],
        })
        events.append(dict(event_type="retrieval", request_id=request_id, user=user, source=source,
                           doc_id=h["doc_id"], classification=h.get("classification"), decision="allow",
                           details={"query": query, "title": h["title"], "chunk": h.get("chunk_index")}))
        if len(results) >= limit:
            break
    await audit.record_many(events)
    log.info("find_then_confirm source=%s user=%s hits=%d confirmed=%d returned=%d in %.2fs", source,
             user.username, len(hits), len(survivors), len(results), time.time() - t0)
    return results


async def _get_live(source: str, source_id: str, ctx: Context) -> dict:
    user, request_id = _caller(ctx)
    item = await _confirm(source, source_id, user)
    doc_id = f"{source}:{source_id}"
    if item is None:
        await audit.record("confirm", request_id=request_id, user=user, source=source, doc_id=doc_id,
                           decision="deny", reason="source_denied_live_read")
        return {"error": "not found or access denied"}
    if source == "jira":
        labels = item["fields"].get("labels") or []
        title = f"{item['key']}: {item['fields'].get('summary', '')}"
        url = f"{_jira.public}/browse/{item['key']}"
        updated = item["fields"].get("updated")
    else:
        labels = [lab["name"] for lab in (await _conf.http.request("GET", f"/api/v2/pages/{source_id}/labels")).get("results", [])]
        title, url = item.get("title", ""), f"{_conf.public}/wiki/pages/viewpage.action?pageId={source_id}"
        updated = (item.get("version") or {}).get("createdAt")
    classification = classification_from_labels(labels)
    decision = (await _policy.decide(user, [{"id": doc_id, "classification": classification, "source": source}]))[0]
    text = _fresh_text(source, item)
    g = check_retrieved(text)
    allowed = decision.get("allow") and g.allowed
    await audit.record("retrieval", request_id=request_id, user=user, source=source, doc_id=doc_id,
                       classification=classification, decision="allow" if allowed else "deny",
                       reason=None if allowed else (decision.get("reason") if not decision.get("allow") else g.reason),
                       details={"title": title, "mode": "direct_read"})
    if not allowed:
        return {"error": "content withheld by policy"}
    return {"doc_id": doc_id, "source": source, "title": title, "url": url, "text": text[:8000],
            "classification": classification, "updated_at": updated}


# ------------------------------------------------------------------ tools
@mcp.tool()
async def search_pages(query: str, ctx: Context, limit: int = 6) -> list[dict]:
    """Search Confluence pages the caller may read. Returns permission-confirmed passages with links."""
    return await _find_then_confirm("confluence", query, ctx, limit)


@mcp.tool()
async def get_page(page_id: str, ctx: Context) -> dict:
    """Read one Confluence page live, as the caller."""
    return await _get_live("confluence", page_id, ctx)


@mcp.tool()
async def list_recent_changes(ctx: Context, days: int = 7, limit: int = 20) -> list[dict]:
    """Confluence pages changed in the last N days that the caller can see (live CQL as the caller)."""
    user, _ = _caller(ctx)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    cql = f'type=page AND lastmodified >= "{since.strftime("%Y-%m-%d %H:%M")}" ORDER BY lastmodified DESC'
    try:
        res = await _conf.search_live(cql, await _source_token(user), limit)
    except SourceDenied:
        return []
    return [{"page_id": r["content"]["id"], "title": r["content"].get("title"),
             "last_modified": r.get("lastModified")} for r in res]


@mcp.tool()
async def search_issues(query: str, ctx: Context, limit: int = 6) -> list[dict]:
    """Search Jira issues (summary, description, comments, resolution) the caller may read."""
    return await _find_then_confirm("jira", query, ctx, limit)


@mcp.tool()
async def get_issue(issue_key: str, ctx: Context) -> dict:
    """Read one Jira issue live, as the caller."""
    return await _get_live("jira", issue_key, ctx)


@mcp.tool()
async def get_linked_issues(issue_key: str, ctx: Context) -> list[dict]:
    """Issues linked to the given issue that the caller may read."""
    user, _ = _caller(ctx)
    item = await _confirm("jira", issue_key, user)
    if item is None:
        return []
    out = []
    for lk in item["fields"].get("issuelinks") or []:
        other = lk.get("outwardIssue") or lk.get("inwardIssue")
        if not other:
            continue
        live = await _confirm("jira", other["key"], user)
        if live is not None:
            out.append({"key": other["key"], "summary": live["fields"].get("summary"),
                        "relation": (lk.get("type") or {}).get("name"),
                        "status": (live["fields"].get("status") or {}).get("name")})
    return out


@mcp.custom_route("/health", methods=["GET"])
async def health(_request):
    from starlette.responses import JSONResponse

    return JSONResponse({"status": "ok"})


app = mcp.streamable_http_app()


def main():
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
