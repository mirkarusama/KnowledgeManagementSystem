"""Writes to the append-only audit ledger (PostgreSQL)."""
from __future__ import annotations

import json
import logging

from psycopg_pool import AsyncConnectionPool

from kms.config import get_settings

log = logging.getLogger("kms.audit")
_pool: AsyncConnectionPool | None = None


async def pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(get_settings().postgres_dsn, min_size=1, max_size=5, open=False)
        await _pool.open()
    return _pool


async def record(event_type: str, *, request_id: str | None = None, user=None, source: str | None = None,
                 doc_id: str | None = None, classification: str | None = None, decision: str = "n/a",
                 reason: str | None = None, details: dict | None = None) -> None:
    await record_many([dict(event_type=event_type, request_id=request_id, user=user, source=source, doc_id=doc_id,
                            classification=classification, decision=decision, reason=reason, details=details)])


async def record_many(events: list[dict]) -> None:
    if not events:
        return
    rows = []
    for e in events:
        u = e.get("user")
        rows.append((e.get("request_id"), e["event_type"], getattr(u, "username", None), getattr(u, "groups", None),
                     e.get("source"), e.get("doc_id"), e.get("classification"), e.get("decision", "n/a"),
                     e.get("reason"), json.dumps(e.get("details") or {}, default=str)))
    try:
        p = await pool()
        async with p.connection() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO audit.events (request_id, event_type, user_id, user_groups, source, doc_id,"
                    " classification, decision, reason, details) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
                    rows,
                )
    except Exception:  # noqa: BLE001
        # Never lose an audit event silently: log it in full so log shipping (Loki/SIEM) still has it.
        log.exception("AUDIT WRITE FAILED: %s", rows)
