"""The search index ("card catalogue") in Qdrant.

Every chunk carries its access-control list as payload:
  acl_0, acl_1, acl_2, acl_3  - each a list of principals ("group:hr", "user:acc-1", "authenticated", "*").
A user may see a chunk only if EVERY level contains one of their principals
(or "*", meaning "this level does not restrict"). Example for Confluence:
  acl_0 = who may read the space, acl_1 = the page's own read restriction,
  acl_2/acl_3 = restrictions inherited from parent pages.
The filter runs INSIDE the vector search, so forbidden chunks are never returned.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from qdrant_client import AsyncQdrantClient, models

from kms.config import get_settings
from kms.index import sparse

ACL_LEVELS = 4
SOURCES = ("confluence", "jira", "servicenow")


def collection(source: str) -> str:
    return f"kms_{source}"


def point_id(doc_id: str, idx: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}#{idx}"))


def acl_payload(levels: list[list[str]]) -> dict:
    if len(levels) > ACL_LEVELS:
        # More nested restrictions than we model: lock the document down to nobody;
        # it will not be found, which is the safe failure.
        return {f"acl_{i}": ["nobody"] for i in range(ACL_LEVELS)}
    out = {}
    for i in range(ACL_LEVELS):
        out[f"acl_{i}"] = sorted(set(levels[i])) if i < len(levels) else ["*"]
    return out


def user_filter(principals: list[str], allowed_labels: list[str], extra: list | None = None) -> models.Filter:
    allowed = list(principals) + ["*"]
    must = [models.FieldCondition(key=f"acl_{i}", match=models.MatchAny(any=allowed)) for i in range(ACL_LEVELS)]
    must.append(models.FieldCondition(key="classification", match=models.MatchAny(any=allowed_labels or ["public"])))
    return models.Filter(must=must + (extra or []))


class Store:
    def __init__(self, client: AsyncQdrantClient | None = None):
        s = get_settings()
        self.client = client or AsyncQdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key or None)
        self.dim = s.embed_dim

    async def ensure(self, source: str) -> None:
        name = collection(source)
        if await self.client.collection_exists(name):
            return
        await self.client.create_collection(
            name,
            vectors_config={"dense": models.VectorParams(size=self.dim, distance=models.Distance.COSINE)},
            sparse_vectors_config={"sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)},
        )
        for field in ["doc_id", "classification", "space", "project", "source"] + [f"acl_{i}" for i in range(ACL_LEVELS)]:
            await self.client.create_payload_index(name, field, models.PayloadSchemaType.KEYWORD)
        await self.client.create_payload_index(name, "chunk_index", models.PayloadSchemaType.INTEGER)
        await self.client.create_payload_index(name, "updated_ts", models.PayloadSchemaType.FLOAT)

    async def upsert_doc(self, source: str, doc: dict, chunks: list[dict], vectors: list[list[float]]) -> int:
        """doc: doc_id, title, url, updated_at (ISO), checksum, classification, acl (list of levels), extra fields."""
        await self.ensure(source)
        await self.delete_doc(source, doc["doc_id"])
        updated = doc.get("updated_at") or datetime.now(timezone.utc).isoformat()
        base = {k: v for k, v in doc.items() if k not in ("acl",)}
        base.update(acl_payload(doc["acl"]))
        base["source"] = source
        base["updated_ts"] = _ts(updated)
        points = []
        for c, v in zip(chunks, vectors):
            idx, vals = sparse.doc_vector(c["embed_text"])
            payload = dict(base, heading=c["heading"], text=c["text"], chunk_index=c["index"])
            points.append(models.PointStruct(
                id=point_id(doc["doc_id"], c["index"]),
                vector={"dense": v, "sparse": models.SparseVector(indices=idx, values=vals)},
                payload=payload,
            ))
        if points:
            await self.client.upsert(collection(source), points=points, wait=True)
        return len(points)

    async def delete_doc(self, source: str, doc_id: str) -> None:
        if not await self.client.collection_exists(collection(source)):
            return
        await self.client.delete(collection(source), points_selector=models.FilterSelector(filter=_doc(doc_id)), wait=True)

    async def set_acl(self, source: str, doc_id: str, acl: list[list[str]], classification: str) -> None:
        payload = acl_payload(acl)
        payload["classification"] = classification
        await self.client.set_payload(collection(source), payload=payload,
                                      points=models.FilterSelector(filter=_doc(doc_id)), wait=True)

    async def manifest(self, source: str) -> dict[str, dict]:
        """doc_id -> {checksum, updated_at, acl_0.., classification} from each document's first chunk."""
        if not await self.client.collection_exists(collection(source)):
            return {}
        out, offset = {}, None
        flt = models.Filter(must=[models.FieldCondition(key="chunk_index", match=models.MatchValue(value=0))])
        while True:
            pts, offset = await self.client.scroll(collection(source), scroll_filter=flt, limit=256, offset=offset,
                                                   with_payload=True, with_vectors=False)
            for p in pts:
                out[p.payload["doc_id"]] = p.payload
            if offset is None:
                return out

    async def search(self, source: str, query: str, dense: list[float], principals: list[str],
                     allowed_labels: list[str], k: int = 8, extra: list | None = None) -> list[dict]:
        name = collection(source)
        if not await self.client.collection_exists(name):
            return []
        flt = user_filter(principals, allowed_labels, extra)
        si, sv = sparse.query_vector(query)
        prefetch = [models.Prefetch(query=dense, using="dense", filter=flt, limit=k * 3)]
        if si:
            prefetch.append(models.Prefetch(query=models.SparseVector(indices=si, values=sv), using="sparse",
                                            filter=flt, limit=k * 3))
        res = await self.client.query_points(name, prefetch=prefetch, query=models.FusionQuery(fusion=models.Fusion.RRF),
                                             query_filter=flt, limit=k, with_payload=True)
        return [dict(p.payload, score=p.score) for p in res.points]


def _doc(doc_id: str) -> models.Filter:
    return models.Filter(must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))])


def _ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
