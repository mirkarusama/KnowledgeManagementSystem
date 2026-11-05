"""A pretend Confluence + Jira, so the whole system can be tried without a real Atlassian site.

It implements the subset of the Atlassian Cloud REST API that the platform uses,
and - importantly - enforces permissions exactly like the real thing would:
space permissions, page restrictions (inherited from parent pages), project
permissions (incl. project roles) and issue security levels.

Admin endpoints (/admin/...) let you change content or permissions during a demo
and fire the matching webhook, to watch the index react.
"""
from __future__ import annotations

import copy
import os
import re
from datetime import datetime, timedelta, timezone

import httpx
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from kms.mock_atlassian import data
from kms.mock_atlassian.personas import PERSONAS

app = FastAPI(title="Mock Atlassian (Confluence + Jira)")
SERVICE_TOKEN = os.getenv("KMS_ATLASSIAN_SERVICE_TOKEN", "svc-dev-token")
JWKS_URL = os.getenv("MOCK_OIDC_JWKS_URL", "http://keycloak:8080/realms/kms/protocol/openid-connect/certs")
TOKEN_AUDIENCE = os.getenv("MOCK_TOKEN_AUDIENCE", "atlassian")
WEBHOOK_URL = os.getenv("MOCK_WEBHOOK_URL", "")  # e.g. http://ingest-receiver:8090/webhooks
ALLOW_DEBUG = os.getenv("KMS_ALLOW_DEBUG_AUTH", "false").lower() == "true"
NOW = datetime.now(timezone.utc)

DB: dict = {}


def reset():
    DB.clear()
    DB["spaces"] = {s["id"]: copy.deepcopy(s) for s in data.SPACES}
    DB["pages"] = {p["id"]: dict(copy.deepcopy(p), version=1, updated=_iso(p["days"])) for p in data.PAGES}
    DB["projects"] = {p["key"]: copy.deepcopy(p) for p in data.PROJECTS}
    DB["issues"] = {i["key"]: dict(copy.deepcopy(i), updated=_iso(i["days"])) for i in data.ISSUES}
    DB["members"] = {u: list(p["groups"]) for u, p in PERSONAS.items()}


def _iso(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


reset()


# ------------------------------------------------------------------ identity
class Caller:
    def __init__(self, service: bool = False, username: str | None = None):
        self.service = service
        self.username = username

    @property
    def account_id(self) -> str | None:
        return PERSONAS[self.username]["account_id"] if self.username in PERSONAS else None

    @property
    def principals(self) -> set[str]:
        if self.service:
            return {"*service*"}
        if self.username not in DB["members"]:
            return set()
        return {f"user:{self.account_id}"} | {f"group:{g}" for g in DB["members"][self.username]}


_jwks: jwt.PyJWKClient | None = None


def caller(authorization: str | None = Header(default=None)) -> Caller:
    if not authorization:
        raise HTTPException(401, "authentication required")
    scheme, _, tok = authorization.partition(" ")
    if scheme.lower() == "basic" or tok == SERVICE_TOKEN:
        return Caller(service=True)
    if tok.startswith("debug:") and ALLOW_DEBUG:
        return Caller(username=tok[len("debug:"):].split(";")[0])
    global _jwks
    try:
        _jwks = _jwks or jwt.PyJWKClient(JWKS_URL)
        claims = jwt.decode(tok, _jwks.get_signing_key_from_jwt(tok).key, algorithms=["RS256"],
                            audience=TOKEN_AUDIENCE, options={"verify_iss": False})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(401, f"invalid token: {e}") from e
    return Caller(username=claims.get("preferred_username"))


def _grants(holders, who: set[str], issue: dict | None = None, project: dict | None = None) -> bool:
    for h in holders:
        t = h[0] if isinstance(h, tuple) else h["type"]
        v = h[1] if isinstance(h, tuple) else h.get("parameter")
        if t == "group" and f"group:{v}" in who:
            return True
        if t == "user" and f"user:{v}" in who:
            return True
        if t == "projectRole" and project:
            for a in project.get("roles", {}).get(v, []):
                if a.get("actorGroup") and f"group:{a['actorGroup']['name']}" in who:
                    return True
                if a.get("actorUser") and f"user:{a['actorUser']['accountId']}" in who:
                    return True
    return False


def page_visible(page: dict, c: Caller) -> bool:
    if c.service:
        return True
    who = c.principals
    if not _grants(DB["spaces"][page["space"]]["read"], who):
        return False
    p = page
    while p:
        if p.get("restrict") and not _grants(p["restrict"], who):
            return False
        p = DB["pages"].get(p["parent"]) if p.get("parent") else None
    return True


def issue_visible(issue: dict, c: Caller) -> bool:
    if c.service:
        return True
    who = c.principals
    proj = DB["projects"][issue["project"]]
    if not _grants(proj["browse"], who, issue, proj):
        return False
    if issue.get("security"):
        return _grants(data.SECURITY_LEVELS[issue["security"]]["members"], who)
    return True


# ---------------------------------------------------------------- Confluence
def _page_json(p: dict, body: bool) -> dict:
    out = {"id": p["id"], "status": "current", "title": p["title"], "spaceId": p["space"], "parentId": p["parent"],
           "version": {"number": p["version"], "createdAt": p["updated"]}}
    if body:
        out["body"] = {"storage": {"value": p["body"], "representation": "storage"}}
    return out


def _get_page(page_id: str, c: Caller) -> dict:
    p = DB["pages"].get(page_id)
    if not p or not page_visible(p, c):
        raise HTTPException(404, "page not found")  # Atlassian hides existence of forbidden content
    return p


@app.get("/wiki/api/v2/spaces")
def spaces(keys: str | None = None, c: Caller = Depends(caller)):
    wanted = set(keys.split(",")) if keys else None
    res = [{"id": s["id"], "key": s["key"], "name": s["name"]} for s in DB["spaces"].values()
           if (not wanted or s["key"] in wanted) and (c.service or _grants(s["read"], c.principals))]
    return {"results": res, "_links": {}}


@app.get("/wiki/api/v2/spaces/{space_id}/pages")
def space_pages(space_id: str, c: Caller = Depends(caller)):
    return {"results": [_page_json(p, False) for p in DB["pages"].values()
                        if p["space"] == space_id and page_visible(p, c)], "_links": {}}


@app.get("/wiki/api/v2/spaces/{space_id}/permissions")
def space_permissions(space_id: str, c: Caller = Depends(caller)):
    if not c.service:
        raise HTTPException(403, "admin only")
    s = DB["spaces"][space_id]
    return {"results": [{"id": f"{space_id}-{i}", "principal": {"type": t, "id": v},
                         "operation": {"key": "read", "targetType": "space"}} for i, (t, v) in enumerate(s["read"])],
            "_links": {}}


@app.get("/wiki/api/v2/pages/{page_id}")
def get_page(page_id: str, c: Caller = Depends(caller), body_format: str | None = Query(None, alias="body-format")):
    return _page_json(_get_page(page_id, c), body=body_format == "storage")


@app.get("/wiki/api/v2/pages/{page_id}/labels")
def page_labels(page_id: str, c: Caller = Depends(caller)):
    return {"results": [{"id": lab, "name": lab, "prefix": "global"} for lab in _get_page(page_id, c)["labels"]]}


@app.get("/wiki/api/v2/pages/{page_id}/ancestors")
def page_ancestors(page_id: str, c: Caller = Depends(caller)):
    p, chain = _get_page(page_id, c), []
    while p.get("parent"):
        p = DB["pages"][p["parent"]]
        chain.insert(0, {"id": p["id"], "type": "page"})
    return {"results": chain}


@app.get("/wiki/rest/api/content/{page_id}/restriction/byOperation/read")
def page_restriction(page_id: str, c: Caller = Depends(caller)):
    p = _get_page(page_id, c)
    users = [{"accountId": v} for t, v in p.get("restrict", []) if t == "user"]
    groups = [{"name": v, "id": v} for t, v in p.get("restrict", []) if t == "group"]
    return {"operation": "read", "restrictions": {"user": {"results": users}, "group": {"results": groups}}}


@app.get("/wiki/rest/api/group/by-id")
def group_by_id(id: str, c: Caller = Depends(caller)):  # noqa: A002
    return {"id": id, "name": id}


class PermCheck(BaseModel):
    subject: dict
    operation: str


@app.post("/wiki/rest/api/content/{page_id}/permission/check")
def permission_check(page_id: str, body: PermCheck, c: Caller = Depends(caller)):
    user = next((u for u, p in PERSONAS.items() if p["account_id"] == body.subject.get("identifier")), None)
    p = DB["pages"].get(page_id)
    return {"hasPermission": bool(p and user and page_visible(p, Caller(username=user)))}


_CQL_DATE = re.compile(r'lastmodified\s*>=\s*"([^"]+)"')


@app.get("/wiki/rest/api/search")
def cql_search(cql: str, limit: int = 25, c: Caller = Depends(caller)):
    since = None
    m = _CQL_DATE.search(cql)
    if m:
        since = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    rows = []
    for p in sorted(DB["pages"].values(), key=lambda x: x["updated"], reverse=True):
        if since and datetime.fromisoformat(p["updated"].replace("Z", "+00:00")) < since:
            continue
        if page_visible(p, c):
            rows.append({"content": {"id": p["id"], "type": "page", "title": p["title"]}, "lastModified": p["updated"]})
    return {"results": rows[:limit], "size": len(rows[:limit])}


# ---------------------------------------------------------------------- Jira
def _adf(text: str) -> dict:
    return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


def _issue_json(i: dict, fields: list[str] | None = None) -> dict:
    proj = DB["projects"][i["project"]]
    f = {
        "summary": i["summary"], "description": _adf(i["description"]), "updated": i["updated"],
        "created": i["updated"], "labels": i.get("labels", []),
        "security": {"id": i["security"], "name": data.SECURITY_LEVELS[i["security"]]["name"]} if i.get("security") else None,
        "status": {"name": i["status"]}, "issuetype": {"name": i["type"]},
        "resolution": {"name": i["resolution"]} if i.get("resolution") else None,
        "project": {"id": proj["id"], "key": proj["key"], "name": proj["name"]},
        "comment": {"comments": [{"body": _adf(t)} for t in i.get("comments", [])]},
        "issuelinks": [{"type": {"name": rel}, "outwardIssue": {"key": k}} for rel, k in i.get("links", [])],
        "reporter": {"accountId": i["reporter"]}, "assignee": {"accountId": i["assignee"]},
    }
    if fields:
        f = {k: v for k, v in f.items() if k in fields}
    return {"id": i["key"], "key": i["key"], "fields": f}


@app.get("/rest/api/3/project/search")
def project_search(c: Caller = Depends(caller)):
    vals = [{"id": p["id"], "key": p["key"], "name": p["name"]} for p in DB["projects"].values()
            if c.service or _grants(p["browse"], c.principals, None, p)]
    return {"values": vals, "isLast": True}


@app.get("/rest/api/3/project/{key}/permissionscheme")
def permission_scheme(key: str, c: Caller = Depends(caller)):
    if not c.service:
        raise HTTPException(403, "admin only")
    p = DB["projects"][key]
    return {"id": 1, "name": f"{key} scheme",
            "permissions": [{"id": n, "holder": h, "permission": "BROWSE_PROJECTS"} for n, h in enumerate(p["browse"])]}


@app.get("/rest/api/3/project/{key}/role/{role_id}")
def project_role(key: str, role_id: str, c: Caller = Depends(caller)):
    return {"id": role_id, "actors": DB["projects"][key].get("roles", {}).get(role_id, [])}


@app.get("/rest/api/3/issuesecurityschemes/level/member")
def level_members(levelId: str, c: Caller = Depends(caller)):  # noqa: N803
    return {"values": [{"holder": h} for h in data.SECURITY_LEVELS[levelId]["members"]], "isLast": True}


class JqlBody(BaseModel):
    jql: str
    fields: list[str] | None = None
    maxResults: int = 50  # noqa: N815
    nextPageToken: str | None = None  # noqa: N815


_JQL_PROJECTS = re.compile(r"project\s+in\s*\(([^)]*)\)", re.I)
_JQL_DATE = re.compile(r'updated\s*>=\s*"([^"]+)"', re.I)


@app.post("/rest/api/3/search/jql")
def jql_search(body: JqlBody, c: Caller = Depends(caller)):
    projects, since = None, None
    if m := _JQL_PROJECTS.search(body.jql):
        projects = {x.strip() for x in m.group(1).split(",")}
    if m := _JQL_DATE.search(body.jql):
        since = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    out = []
    for i in sorted(DB["issues"].values(), key=lambda x: x["key"]):
        if projects and i["project"] not in projects:
            continue
        if since and datetime.fromisoformat(i["updated"].replace("Z", "+00:00")) < since:
            continue
        if issue_visible(i, c):
            out.append(_issue_json(i, body.fields))
    return {"issues": out[: body.maxResults]}


@app.get("/rest/api/3/issue/{key}")
def get_issue(key: str, fields: str | None = None, c: Caller = Depends(caller)):
    i = DB["issues"].get(key)
    if not i or not issue_visible(i, c):
        raise HTTPException(404, "Issue does not exist or you do not have permission to see it.")
    return _issue_json(i, fields.split(",") if fields else None)


@app.get("/rest/api/3/user/permission/search")
def user_permission_search(accountId: str, permissions: str, issueKey: str, c: Caller = Depends(caller)):  # noqa: N803
    user = next((u for u, p in PERSONAS.items() if p["account_id"] == accountId), None)
    i = DB["issues"].get(issueKey)
    return [{"accountId": accountId}] if user and i and issue_visible(i, Caller(username=user)) else []


# --------------------------------------------------------------------- browser views (demo only)
_VIEW = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>{title}</title><style>body{{font:16px/1.6 system-ui;max-width:760px;margin:24px auto;padding:0 16px}}
.bar{{background:#fff4dc;color:#8a5a00;padding:8px 12px;border-radius:8px;font-size:14px}}</style>
<div class=bar>Pretend {kind} for the demo. A real {kind} would ask you to sign in and check your access.</div>
<h1>{title}</h1>{body}"""


def _render_page(pid: str) -> str:
    p = DB["pages"].get(pid)
    if not p:
        raise HTTPException(404)
    return _VIEW.format(title=p["title"], body=p["body"], kind="Confluence")


@app.get("/wiki/spaces/{space_key}/pages/{page_id}", response_class=HTMLResponse)
def view_page(space_key: str, page_id: str):
    return _render_page(page_id)


@app.get("/wiki/pages/viewpage.action", response_class=HTMLResponse)
def view_page_by_id(page_id: str = Query(alias="pageId")):
    return _render_page(page_id)


@app.get("/browse/{key}", response_class=HTMLResponse)
def view_issue(key: str):
    i = DB["issues"].get(key)
    if not i:
        raise HTTPException(404)
    comments = "".join(f"<li>{c}</li>" for c in i.get("comments", []))
    body = (f"<p><b>{i['type']}</b> · Status: {i['status']}</p><p>{i['description']}</p>"
            + (f"<h3>Comments</h3><ul>{comments}</ul>" if comments else ""))
    return _VIEW.format(title=f"{key}: {i['summary']}", body=body, kind="Jira")


# --------------------------------------------------------------------- admin (demo controls)
class PageEdit(BaseModel):
    title: str | None = None
    body: str | None = None


class Restriction(BaseModel):
    groups: list[str] = []
    users: list[str] = []


class Membership(BaseModel):
    groups: list[str]


async def _webhook(source: str, payload: dict):
    if not WEBHOOK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(f"{WEBHOOK_URL}/{source}", json=payload)
    except httpx.HTTPError:
        pass


@app.post("/admin/confluence/pages/{page_id}")
async def admin_edit_page(page_id: str, body: PageEdit):
    p = DB["pages"][page_id]
    if body.title:
        p["title"] = body.title
    if body.body:
        p["body"] = body.body
    p["version"] += 1
    p["updated"] = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    await _webhook("confluence", {"event": "page_updated", "page": {"id": page_id}})
    return _page_json(p, True)


@app.post("/admin/confluence/pages/{page_id}/restrict")
async def admin_restrict(page_id: str, body: Restriction):
    p = DB["pages"][page_id]
    p["restrict"] = [("group", g) for g in body.groups] + [("user", u) for u in body.users]
    await _webhook("confluence", {"event": "content_restrictions_updated", "page": {"id": page_id}})
    return {"id": page_id, "restrict": p["restrict"]}


@app.post("/admin/members/{username}")
async def admin_membership(username: str, body: Membership):
    """Simulates the directory changing someone's groups (e.g. access revoked)."""
    DB["members"][username] = body.groups
    return {"username": username, "groups": body.groups}


@app.post("/admin/reset")
async def admin_reset():
    reset()
    return {"ok": True}


@app.get("/health")
async def health(request: Request):
    return {"status": "ok"}
