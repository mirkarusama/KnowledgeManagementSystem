"""Scheduled and event-driven jobs. Airflow (production) and the light dev scheduler both call these.

  full_index     - first load of everything in pilot scope
  acl_sync       - every 15 min: re-read permissions, update the index (pauses if >5% would vanish)
  reconcile      - nightly: compare source manifest vs index -> NEW / UPDATED / MISSING / STALE
  poll_changes   - every 5 min (Atlassian Cloud, no inbound webhooks): CQL/JQL "changed since" -> queue
  archive_audit  - daily: copy audit rows to write-once object storage
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import redis.asyncio as aioredis

from kms import audit
from kms.config import get_settings
from kms.ingest.indexer import Indexer
from kms.sources.atlassian import Confluence, Jira

log = logging.getLogger("kms.jobs")
PILOT_SOURCES = ("confluence", "jira")


class SyncPaused(Exception):
    pass


async def full_index(sources=PILOT_SOURCES) -> dict:
    ix = Indexer()
    counts = {}
    for src in sources:
        ids = await ix.list_source_ids(src)
        total = 0
        for sid in ids:
            total += await ix.index(src, sid)
        counts[src] = {"documents": len(ids), "chunks": total}
    await audit.record("sync", details={"job": "full_index", "counts": counts})
    return counts


def _guard(source: str, missing: int, total: int, job: str):
    limit = get_settings().acl_removal_guard
    if total and missing / total > limit:
        msg = (f"{job} paused for {source}: {missing}/{total} documents ({missing / total:.0%}) would be removed, "
               f"above the {limit:.0%} safety limit. Check the source API / service account before forcing.")
        log.error(msg)
        raise SyncPaused(msg)


async def acl_sync(sources=PILOT_SOURCES, force: bool = False) -> dict:
    ix = Indexer()  # fresh clients = fresh permission caches
    report = {}
    for src in sources:
        indexed = await ix.store.manifest(src)
        results: dict[str, list] = {"SAME": [], "ACL": [], "UPDATED": [], "NEW": [], "MISSING": []}
        for doc_id, payload in indexed.items():
            sid = doc_id.split(":", 1)[1]
            results[await ix.refresh_acl(src, sid, payload)].append(doc_id)
        try:
            if not force:
                _guard(src, len(results["MISSING"]), len(indexed), "acl_sync")
        except SyncPaused as e:
            await audit.record("sync", source=src, decision="deny", reason="paused_removal_guard",
                               details={"job": "acl_sync", "message": str(e)})
            report[src] = {"paused": True, "missing": len(results["MISSING"])}
            continue
        for doc_id in results["MISSING"]:
            await ix.delete(src, doc_id.split(":", 1)[1])
        report[src] = {k: len(v) for k, v in results.items()}
    await audit.record("sync", details={"job": "acl_sync", "report": report})
    return report


async def reconcile(sources=PILOT_SOURCES, force: bool = False) -> dict:
    ix = Indexer()
    report = {}
    for src in sources:
        source_ids = await ix.list_source_ids(src)
        indexed = await ix.store.manifest(src)
        indexed_ids = {d.split(":", 1)[1]: p for d, p in indexed.items()}
        new = [s for s in source_ids if s not in indexed_ids]
        missing = [s for s in indexed_ids if s not in source_ids]
        changed = [s for s in source_ids if s in indexed_ids and source_ids[s] and
                   source_ids[s] != indexed_ids[s].get("updated_at")]
        try:
            if not force:
                _guard(src, len(missing), len(indexed_ids), "reconcile")
        except SyncPaused as e:
            await audit.record("sync", source=src, decision="deny", reason="paused_removal_guard",
                               details={"job": "reconcile", "message": str(e)})
            report[src] = {"paused": True}
            continue
        updated, stale = 0, 0
        for sid in new:
            await ix.index(src, sid)
        for sid in missing:
            await ix.delete(src, sid)
        for sid in changed:
            # UPDATED = content changed; STALE = only metadata/timestamp drifted (e.g. backdating)
            outcome = await ix.refresh_acl(src, sid, indexed_ids[sid])
            if outcome == "UPDATED":
                updated += 1
            else:
                stale += 1
                await ix.index(src, sid)
        report[src] = {"NEW": len(new), "UPDATED": updated, "MISSING": len(missing), "STALE": stale}
    await audit.record("sync", details={"job": "reconcile", "report": report})
    return report


async def poll_changes() -> dict:
    """Atlassian Cloud change poll: no inbound port needed."""
    s = get_settings()
    r = aioredis.from_url(s.valkey_url)
    now = datetime.now(timezone.utc)
    out = {}
    for src, client in (("confluence", Confluence()), ("jira", Jira())):
        key = f"kms:poll:{src}"
        raw = await r.get(key)
        since = datetime.fromisoformat(raw.decode()) if raw else now - timedelta(days=1)
        ids = await client.changed_since(since - timedelta(minutes=2))  # overlap to avoid gaps
        for sid in ids:
            await r.xadd(s.ingest_stream, {"source": src, "action": "upsert", "id": sid, "origin": "poll"})
        await r.set(key, now.isoformat())
        out[src] = len(ids)
    await r.aclose()
    return out


async def archive_audit() -> dict:
    """Copy yesterday's (and older) audit rows to S3-compatible storage with Object Lock (WORM)."""
    import os

    bucket = os.getenv("KMS_AUDIT_S3_BUCKET")
    if not bucket:
        return {"skipped": "KMS_AUDIT_S3_BUCKET not set"}
    import boto3  # optional dependency, installed in production images

    s3 = boto3.client("s3", endpoint_url=os.getenv("KMS_AUDIT_S3_ENDPOINT") or None)
    retain_days = int(os.getenv("KMS_AUDIT_RETENTION_DAYS", "2555"))
    p = await audit.pool()
    async with p.connection() as conn:
        cur = await conn.execute(
            "SELECT id, ts, request_id, event_type, user_id, user_groups, source, doc_id, classification, decision,"
            " reason, details FROM audit.events WHERE archived = false AND ts < date_trunc('day', now()) ORDER BY id")
        rows = await cur.fetchall()
        if not rows:
            return {"archived": 0}
        cols = [d.name for d in cur.description]
        body = "\n".join(json.dumps(dict(zip(cols, r)), default=str) for r in rows).encode()
        key = f"audit/{datetime.now(timezone.utc):%Y/%m/%d}/events-{rows[0][0]}-{rows[-1][0]}.jsonl"
        s3.put_object(Bucket=bucket, Key=key, Body=body, ObjectLockMode="COMPLIANCE",
                      ObjectLockRetainUntilDate=datetime.now(timezone.utc) + timedelta(days=retain_days))
        await conn.execute("UPDATE audit.events SET archived = true WHERE archived = false AND id <= %s", (rows[-1][0],))
    return {"archived": len(rows), "object": key}


def run(coro):
    return asyncio.run(coro)
