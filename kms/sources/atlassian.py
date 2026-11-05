"""Read-only clients for Confluence (REST v2 + CQL) and Jira (REST v3 + JQL).

Two ways of calling the source:
  * as the service account - used by ingestion to read content and permissions;
  * as the user           - used by the CONFIRM step, so Confluence/Jira itself
                             decides whether this person may see the result.

Paths follow Atlassian Cloud. The bundled mock server implements the same
paths, so everything can be tried without a real Atlassian site.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from kms.config import get_settings
from kms.index.chunking import adf_text

JIRA_FIELDS = ["summary", "description", "updated", "created", "labels", "security", "status", "project",
               "comment", "issuelinks", "reporter", "assignee", "issuetype", "resolution"]


class SourceDenied(Exception):
    """The source system said this user may not read this item (or it no longer exists)."""


class CircuitOpen(Exception):
    pass


@dataclass
class _Breaker:
    """Stops hammering a source that is failing: opens at >10% errors over 5 minutes."""
    window_s: int = 300
    threshold: float = 0.10
    min_calls: int = 20
    calls: list = field(default_factory=list)
    open_until: float = 0.0

    def record(self, ok: bool):
        now = time.time()
        self.calls = [(t, o) for t, o in self.calls if now - t < self.window_s] + [(now, ok)]
        errors = sum(1 for _, o in self.calls if not o)
        if len(self.calls) >= self.min_calls and errors / len(self.calls) > self.threshold:
            self.open_until = now + 60

    def check(self):
        if time.time() < self.open_until:
            raise CircuitOpen("source temporarily unavailable (circuit open)")


@dataclass
class _Bucket:
    """Token-bucket rate limit per source (requests per second)."""
    rate: float = 10.0
    tokens: float = 10.0
    last: float = field(default_factory=time.monotonic)

    async def take(self):
        while True:
            now = time.monotonic()
            self.tokens = min(self.rate, self.tokens + (now - self.last) * self.rate)
            self.last = now
            if self.tokens >= 1:
                self.tokens -= 1
                return
            await asyncio.sleep((1 - self.tokens) / self.rate)


_breakers: dict[str, _Breaker] = {}
_buckets: dict[str, _Bucket] = {}


class _Http:
    def __init__(self, base: str, name: str):
        self.base = base.rstrip("/")
        self.name = name
        _breakers.setdefault(name, _Breaker())
        _buckets.setdefault(name, _Bucket())

    def _service_auth(self) -> dict:
        s = get_settings()
        if s.atlassian_service_user:  # Atlassian Cloud: email + API token
            import base64
            raw = f"{s.atlassian_service_user}:{s.atlassian_service_token}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
        return {"Authorization": f"Bearer {s.atlassian_service_token}"}  # Data Center PAT / mock

    async def request(self, method: str, path: str, *, user_token: str | None = None, **kw):
        _breakers[self.name].check()
        headers = {"Accept": "application/json"}
        headers.update({"Authorization": f"Bearer {user_token}"} if user_token else self._service_auth())
        delay = 1.0
        for attempt in range(4):
            await _buckets[self.name].take()
            try:
                async with httpx.AsyncClient(timeout=20) as c:
                    r = await c.request(method, self.base + path, headers=headers, **kw)
            except httpx.HTTPError:
                _breakers[self.name].record(False)
                if attempt == 3:
                    raise
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if r.status_code in (429, 502, 503, 504):
                _breakers[self.name].record(False)
                await asyncio.sleep(float(r.headers.get("Retry-After", delay)))
                delay *= 2
                continue
            _breakers[self.name].record(r.status_code < 500)
            if r.status_code in (401, 403, 404):
                raise SourceDenied(f"{self.name} {r.status_code} for {path}")
            r.raise_for_status()
            return r.json() if r.content else {}
        raise httpx.HTTPError(f"{self.name}: gave up after retries on {path}")


def classification_from_labels(labels: list[str]) -> str:
    s = get_settings()
    for lab in labels:
        if lab.lower().startswith(s.classification_label_prefix):
            return lab.lower()[len(s.classification_label_prefix):].replace("-", "_")
    return s.default_classification


def checksum(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]


# ---------------------------------------------------------------- Confluence
class Confluence:
    source = "confluence"

    def __init__(self, base: str | None = None):
        self.base = (base or get_settings().confluence_url).rstrip("/")
        self.public = (get_settings().confluence_public_url or self.base).rstrip("/")
        self.http = _Http(self.base + "/wiki", "confluence")
        self._group_names: dict[str, str] = {}
        self._space_acl: dict[str, list[str]] = {}
        self._spaces: dict[str, dict] = {}

    async def spaces(self) -> list[dict]:
        keys = get_settings().list_or_none(get_settings().confluence_spaces)
        params = {"limit": 250} | ({"keys": ",".join(keys)} if keys else {})
        out = await self._paged("/api/v2/spaces", params)
        self._spaces.update({s["id"]: s for s in out})
        return out

    async def _paged(self, path: str, params: dict) -> list[dict]:
        out = []
        while path:
            data = await self.http.request("GET", path, params=params)
            out += data.get("results", [])
            nxt = (data.get("_links") or {}).get("next")
            if not nxt:
                break
            path, params = nxt.removeprefix("/wiki"), None
        return out

    async def list_page_ids(self, space_id: str) -> list[dict]:
        return await self._paged(f"/api/v2/spaces/{space_id}/pages", {"limit": 250})

    async def _group_name(self, gid: str) -> str:
        if gid not in self._group_names:
            try:
                g = await self.http.request("GET", "/rest/api/group/by-id", params={"id": gid})
                self._group_names[gid] = g.get("name", gid)
            except Exception:  # noqa: BLE001
                self._group_names[gid] = gid
        return self._group_names[gid]

    async def space_read_principals(self, space_id: str, refresh: bool = False) -> list[str]:
        if refresh or space_id not in self._space_acl:
            perms = await self._paged(f"/api/v2/spaces/{space_id}/permissions", {"limit": 250})
            principals = []
            for p in perms:
                op = p.get("operation", {})
                if op.get("key") != "read" or op.get("targetType") != "space":
                    continue
                pr = p.get("principal", {})
                if pr.get("type") == "group":
                    principals.append(f"group:{await self._group_name(pr['id'])}")
                elif pr.get("type") == "user":
                    principals.append(f"user:{pr['id']}")
            self._space_acl[space_id] = principals or ["nobody"]
        return self._space_acl[space_id]

    async def _restriction(self, page_id: str) -> list[str] | None:
        """Read restriction on one page; None = unrestricted."""
        data = await self.http.request("GET", f"/rest/api/content/{page_id}/restriction/byOperation/read",
                                       params={"expand": "restrictions.user,restrictions.group"})
        r = data.get("restrictions", {})
        users = [f"user:{u['accountId']}" for u in r.get("user", {}).get("results", [])]
        groups = [f"group:{g['name']}" for g in r.get("group", {}).get("results", [])]
        return (users + groups) or None

    async def acl(self, page: dict) -> list[list[str]]:
        levels = [await self.space_read_principals(page["spaceId"])]
        own = await self._restriction(page["id"])
        if own:
            levels.append(own)
        anc = await self.http.request("GET", f"/api/v2/pages/{page['id']}/ancestors", params={"limit": 50})
        for a in reversed(anc.get("results", [])):  # nearest parent first
            r = await self._restriction(a["id"])
            if r:
                levels.append(r)
        return levels

    async def fetch(self, page_id: str, user_token: str | None = None) -> dict:
        p = await self.http.request("GET", f"/api/v2/pages/{page_id}", params={"body-format": "storage"},
                                    user_token=user_token)
        return p

    async def document(self, page_id: str) -> dict:
        """Everything the indexer needs about one page (read as the service account)."""
        p = await self.fetch(page_id)
        labels = [lab["name"] for lab in (await self.http.request("GET", f"/api/v2/pages/{page_id}/labels")).get("results", [])]
        if p["spaceId"] not in self._spaces:
            await self.spaces()
        space = self._spaces.get(p["spaceId"], {"key": p["spaceId"]})
        body = (p.get("body") or {}).get("storage", {}).get("value", "")
        updated = (p.get("version") or {}).get("createdAt") or datetime.now(timezone.utc).isoformat()
        return {
            "doc_id": f"confluence:{p['id']}",
            "source_id": p["id"],
            "title": p["title"],
            "url": f"{self.public}/wiki/spaces/{space['key']}/pages/{p['id']}",
            "space": space["key"],
            "updated_at": updated,
            "labels": labels,
            "classification": classification_from_labels(labels),
            "checksum": checksum(p["title"], body),
            "acl": await self.acl(p),
            "html": body,
        }

    async def changed_since(self, since: datetime) -> list[str]:
        cql = f'type=page AND lastmodified >= "{since.strftime("%Y-%m-%d %H:%M")}"'
        data = await self.http.request("GET", "/rest/api/search", params={"cql": cql, "limit": 200})
        return [r["content"]["id"] for r in data.get("results", []) if r.get("content", {}).get("type") == "page"]

    async def search_live(self, cql: str, user_token: str, limit: int = 10) -> list[dict]:
        data = await self.http.request("GET", "/rest/api/search", params={"cql": cql, "limit": limit},
                                       user_token=user_token)
        return data.get("results", [])

    async def user_can_read(self, page_id: str, account_id: str) -> bool:
        r = await self.http.request("POST", f"/rest/api/content/{page_id}/permission/check",
                                    json={"subject": {"type": "user", "identifier": account_id}, "operation": "read"})
        return bool(r.get("hasPermission"))


# ---------------------------------------------------------------------- Jira
class Jira:
    source = "jira"

    def __init__(self, base: str | None = None):
        self.base = (base or get_settings().jira_url).rstrip("/")
        self.public = (get_settings().jira_public_url or self.base).rstrip("/")
        self.http = _Http(self.base, "jira")
        self._project_holders: dict[str, list[dict]] = {}
        self._level_holders: dict[str, list[dict]] = {}

    async def projects(self) -> list[dict]:
        keys = get_settings().list_or_none(get_settings().jira_projects)
        out, start = [], 0
        while True:
            data = await self.http.request("GET", "/rest/api/3/project/search", params={"startAt": start, "maxResults": 50})
            out += data.get("values", [])
            if data.get("isLast", True):
                break
            start += 50
        return [p for p in out if not keys or p["key"] in keys]

    async def search(self, jql: str, fields: list[str] | None = None, limit: int = 1000,
                     user_token: str | None = None) -> list[dict]:
        out, token = [], None
        while len(out) < limit:
            body = {"jql": jql, "fields": fields or ["updated"], "maxResults": min(100, limit - len(out))}
            if token:
                body["nextPageToken"] = token
            data = await self.http.request("POST", "/rest/api/3/search/jql", json=body, user_token=user_token)
            out += data.get("issues", [])
            token = data.get("nextPageToken")
            if not token:
                break
        return out

    async def _holders_to_principals(self, project_key: str, holders: list[dict], issue: dict) -> list[str]:
        out = []
        f = issue.get("fields", {})
        for h in holders:
            t, param = h.get("type"), h.get("parameter") or h.get("value")
            if t == "group":
                out.append(f"group:{param}")
            elif t == "user":
                out.append(f"user:{param}")
            elif t in ("applicationRole", "anyone", "sd.customer.portal.only"):
                out.append("authenticated")
            elif t == "projectRole":
                role = await self.http.request("GET", f"/rest/api/3/project/{project_key}/role/{param}")
                for a in role.get("actors", []):
                    if a.get("actorGroup"):
                        out.append(f"group:{a['actorGroup'].get('name') or a['actorGroup'].get('displayName')}")
                    elif a.get("actorUser"):
                        out.append(f"user:{a['actorUser']['accountId']}")
            elif t in ("reporter", "assignee") and f.get(t):
                out.append(f"user:{f[t]['accountId']}")
        return out or ["nobody"]

    async def acl(self, issue: dict) -> list[list[str]]:
        pkey = issue["fields"]["project"]["key"]
        if pkey not in self._project_holders:
            scheme = await self.http.request("GET", f"/rest/api/3/project/{pkey}/permissionscheme",
                                             params={"expand": "permissions"})
            self._project_holders[pkey] = [p["holder"] for p in scheme.get("permissions", [])
                                           if p.get("permission") == "BROWSE_PROJECTS"]
        levels = [await self._holders_to_principals(pkey, self._project_holders[pkey], issue)]
        sec = issue["fields"].get("security")
        if sec:
            lid = str(sec["id"])
            if lid not in self._level_holders:
                data = await self.http.request("GET", "/rest/api/3/issuesecurityschemes/level/member",
                                               params={"levelId": lid, "maxResults": 100})
                self._level_holders[lid] = [v["holder"] for v in data.get("values", [])]
            levels.append(await self._holders_to_principals(pkey, self._level_holders[lid], issue))
        return levels

    async def fetch(self, key: str, user_token: str | None = None) -> dict:
        return await self.http.request("GET", f"/rest/api/3/issue/{key}", params={"fields": ",".join(JIRA_FIELDS)},
                                       user_token=user_token)

    @staticmethod
    def issue_text(issue: dict) -> str:
        f = issue.get("fields", {})
        parts = [f"Status: {(f.get('status') or {}).get('name', '')}",
                 f"Type: {(f.get('issuetype') or {}).get('name', '')}"]
        if f.get("resolution"):
            parts.append(f"Resolution: {f['resolution'].get('name', '')}")
        parts += ["", adf_text(f.get("description"))]
        for c in (f.get("comment") or {}).get("comments", []):
            parts.append(f"Comment: {adf_text(c.get('body'))}")
        return "\n".join(parts).strip()

    async def document(self, key: str) -> dict:
        issue = await self.fetch(key)
        f = issue["fields"]
        text = self.issue_text(issue)
        labels = f.get("labels") or []
        return {
            "doc_id": f"jira:{issue['key']}",
            "source_id": issue["key"],
            "title": f"{issue['key']}: {f.get('summary', '')}",
            "url": f"{self.public}/browse/{issue['key']}",
            "project": f["project"]["key"],
            "status": (f.get("status") or {}).get("name", ""),
            "updated_at": f.get("updated"),
            "labels": labels,
            "classification": classification_from_labels(labels),
            "checksum": checksum(f.get("summary", ""), text),
            "acl": await self.acl(issue),
            "text": text,
            "links": [lk.get("outwardIssue", lk.get("inwardIssue", {})).get("key")
                      for lk in f.get("issuelinks") or [] if lk.get("outwardIssue") or lk.get("inwardIssue")],
        }

    async def changed_since(self, since: datetime) -> list[str]:
        jql = f'updated >= "{since.strftime("%Y-%m-%d %H:%M")}" ORDER BY updated ASC'
        return [i["key"] for i in await self.search(jql)]

    async def all_keys(self) -> list[str]:
        keys = [p["key"] for p in await self.projects()]
        if not keys:
            return []
        return [i["key"] for i in await self.search(f"project in ({','.join(keys)}) ORDER BY key")]

    async def user_can_read(self, key: str, account_id: str) -> bool:
        r = await self.http.request("GET", "/rest/api/3/user/permission/search",
                                    params={"accountId": account_id, "permissions": "BROWSE_PROJECTS", "issueKey": key})
        return any(u.get("accountId") == account_id for u in (r if isinstance(r, list) else []))
