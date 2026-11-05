"""Asks Open Policy Agent (the 'rulebook') for decisions. Fails closed."""
from __future__ import annotations

import httpx

from kms.auth import User
from kms.config import get_settings

LEVELS = ["public", "internal", "confidential", "highly_confidential", "restricted"]


class PolicyClient:
    def __init__(self, url: str | None = None):
        self.url = (url or get_settings().opa_url).rstrip("/")

    async def _query(self, path: str, payload: dict):
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(f"{self.url}/v1/data/kms/{path}", json={"input": payload})
            r.raise_for_status()
            return r.json().get("result")

    async def allowed_labels(self, user: User) -> list[str]:
        try:
            res = await self._query("allowed_labels", {"user": {"groups": user.groups}})
            return list(res or [])
        except Exception:  # noqa: BLE001
            return ["public"]  # fail closed: if the rulebook is unreachable, show only public

    async def decide(self, user: User, resources: list[dict]) -> list[dict]:
        if not resources:
            return []
        try:
            res = await self._query("decisions", {"user": {"groups": user.groups, "username": user.username},
                                                  "resources": resources})
            return list(res or [])
        except Exception as e:  # noqa: BLE001
            return [{"id": r["id"], "allow": False, "reason": f"policy_unavailable: {e}"} for r in resources]
