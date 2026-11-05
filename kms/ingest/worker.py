"""Ingestion worker: takes jobs off the queue and (re-)indexes the affected item."""
from __future__ import annotations

import asyncio
import logging
import socket

import redis.asyncio as aioredis
from redis.exceptions import ResponseError

from kms.config import get_settings
from kms.ingest.indexer import Indexer

log = logging.getLogger("kms.worker")
GROUP = "indexers"
MAX_ATTEMPTS = 3


async def handle(ix: Indexer, job: dict) -> None:
    src, action, sid = job["source"], job["action"], job["id"]
    if action == "delete":
        await ix.delete(src, sid)
    elif action == "space_acl":
        # A space's permissions changed: refresh ACL payload of every page in that space.
        manifest = await ix.store.manifest(src)
        for doc_id, payload in manifest.items():
            if payload.get("space") == sid:
                await ix.refresh_acl(src, doc_id.split(":", 1)[1], payload)
    else:
        await ix.index(src, sid)


async def run_worker(stop_after_idle: int | None = None) -> None:
    s = get_settings()
    r = aioredis.from_url(s.valkey_url, decode_responses=True, socket_timeout=30)
    try:
        await r.xgroup_create(s.ingest_stream, GROUP, id="0", mkstream=True)
    except ResponseError:
        pass  # group exists
    me = socket.gethostname()
    ix = Indexer()
    idle = 0
    log.info("worker %s listening on %s", me, s.ingest_stream)
    while True:
        # First retry anything pending for us, then read new messages.
        resp = await r.xreadgroup(GROUP, me, {s.ingest_stream: "0"}, count=10) or []
        if not resp or not resp[0][1]:
            resp = await r.xreadgroup(GROUP, me, {s.ingest_stream: ">"}, count=10, block=5000) or []
        msgs = resp[0][1] if resp else []
        if not msgs:
            idle += 1
            if stop_after_idle and idle >= stop_after_idle:
                return
            continue
        idle = 0
        for msg_id, job in msgs:
            try:
                await handle(ix, job)
                await r.xack(s.ingest_stream, GROUP, msg_id)
            except Exception:  # noqa: BLE001
                attempts = int(await r.hincrby("kms:ingest:attempts", msg_id, 1))
                log.exception("job %s failed (attempt %d): %s", msg_id, attempts, job)
                if attempts >= MAX_ATTEMPTS:
                    await r.xadd("kms:ingest:dead", job | {"failed_id": msg_id})
                    await r.xack(s.ingest_stream, GROUP, msg_id)
                    await r.hdel("kms:ingest:attempts", msg_id)
                else:
                    await asyncio.sleep(2 ** attempts)
