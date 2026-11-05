"""Webhook receiver: Confluence/Jira say "something changed" -> we put a job on the queue.

Plain English: a mail slot. Change notices drop in and are queued (Valkey
Streams) so workers can re-index the affected page within minutes.
"""
from __future__ import annotations

import hashlib
import hmac

import redis.asyncio as aioredis
from fastapi import FastAPI, Header, HTTPException, Request

from kms.config import get_settings

app = FastAPI(title="KMS webhook receiver")
_r: aioredis.Redis | None = None


def r() -> aioredis.Redis:
    global _r
    if _r is None:
        _r = aioredis.from_url(get_settings().valkey_url)
    return _r


def _verify(body: bytes, signature: str | None):
    secret = get_settings().webhook_secret
    if not secret:
        return
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "bad signature")


CONFLUENCE_DELETE = {"page_removed", "page_trashed", "page_deleted"}
CONFLUENCE_SPACE_ACL = {"space_permissions_updated", "space_updated"}


def map_event(source: str, payload: dict) -> list[dict]:
    if source == "confluence":
        event = payload.get("event") or payload.get("eventType") or ""
        page = payload.get("page") or payload.get("content") or {}
        if event in CONFLUENCE_SPACE_ACL:
            space = payload.get("space", {}).get("key", "")
            return [{"source": "confluence", "action": "space_acl", "id": space}]
        if not page.get("id"):
            return []
        action = "delete" if event in CONFLUENCE_DELETE else "upsert"  # incl. content_restrictions_updated
        return [{"source": "confluence", "action": action, "id": str(page["id"])}]
    if source == "jira":
        event = payload.get("webhookEvent", "")
        key = (payload.get("issue") or {}).get("key")
        if not key:
            return []
        return [{"source": "jira", "action": "delete" if event == "jira:issue_deleted" else "upsert", "id": key}]
    return []


@app.post("/webhooks/{source}")
async def webhook(source: str, request: Request, x_hub_signature: str | None = Header(default=None)):
    body = await request.body()
    _verify(body, x_hub_signature)
    jobs = map_event(source, await request.json())
    for job in jobs:
        await r().xadd(get_settings().ingest_stream, job | {"origin": "webhook"})
    return {"queued": len(jobs)}


@app.get("/health")
async def health():
    return {"status": "ok"}
