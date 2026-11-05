"""Light scheduler for laptops / dev. In production the same jobs run as Airflow DAGs (airflow/dags)."""
from __future__ import annotations

import asyncio
import logging
import time

from kms.config import get_settings
from kms.ingest import jobs

log = logging.getLogger("kms.scheduler")


async def run_scheduler() -> None:
    s = get_settings()
    schedule = [
        ("acl_sync", s.acl_sync_interval_s, jobs.acl_sync),
        ("reconcile", 24 * 3600, jobs.reconcile),
        ("archive_audit", 24 * 3600, jobs.archive_audit),
    ]
    if s.atlassian_deployment == "cloud":
        schedule.append(("poll_changes", s.poll_interval_s, jobs.poll_changes))
    last = {name: time.time() for name, _, _ in schedule}
    log.info("scheduler started: %s", [(n, i) for n, i, _ in schedule])
    while True:
        for name, interval, fn in schedule:
            if time.time() - last[name] >= interval:
                last[name] = time.time()
                try:
                    log.info("%s: %s", name, await fn())
                except Exception:  # noqa: BLE001
                    log.exception("%s failed", name)
        await asyncio.sleep(10)
