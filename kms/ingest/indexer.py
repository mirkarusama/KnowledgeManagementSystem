"""Reads a page/issue as the service account and writes it into the index with its permissions."""
from __future__ import annotations

import logging

from kms.index.chunking import Section, chunk_sections, html_sections
from kms.index.qdrant_store import Store
from kms.llm import Embedder
from kms.sources.atlassian import Confluence, Jira, SourceDenied

log = logging.getLogger("kms.indexer")


class Indexer:
    def __init__(self, store: Store | None = None, embedder: Embedder | None = None):
        self.store = store or Store()
        self.embedder = embedder or Embedder()
        self.clients = {"confluence": Confluence(), "jira": Jira()}

    async def index(self, source: str, source_id: str) -> int:
        client = self.clients[source]
        try:
            doc = await client.document(source_id)
        except SourceDenied:
            # Gone at the source (or the service account lost access): remove from the index.
            await self.store.delete_doc(source, f"{source}:{source_id}")
            log.info("removed %s:%s (no longer readable at source)", source, source_id)
            return 0
        if source == "confluence":
            sections = html_sections(doc.pop("html"))
        else:
            sections = [Section("Details", doc.pop("text"))]
        chunks = chunk_sections(doc["title"], sections)
        vectors = await self.embedder.embed([c["embed_text"] for c in chunks])
        n = await self.store.upsert_doc(source, doc, chunks, vectors)
        log.info("indexed %s (%d chunks, acl=%s, class=%s)", doc["doc_id"], n, doc["acl"], doc["classification"])
        return n

    async def delete(self, source: str, source_id: str) -> None:
        await self.store.delete_doc(source, f"{source}:{source_id}")

    async def refresh_acl(self, source: str, source_id: str, indexed: dict | None = None) -> str:
        """Re-read permissions; re-embed only if the content changed. Returns NEW/UPDATED/ACL/MISSING/SAME."""
        client = self.clients[source]
        try:
            doc = await client.document(source_id)
        except SourceDenied:
            return "MISSING"
        if not indexed or indexed.get("checksum") != doc["checksum"]:
            await self.index(source, source_id)
            return "NEW" if not indexed else "UPDATED"
        from kms.index.qdrant_store import acl_payload

        new_acl = acl_payload(doc["acl"])
        if any(sorted(indexed.get(k, [])) != v for k, v in new_acl.items()) or \
                indexed.get("classification") != doc["classification"]:
            await self.store.set_acl(source, doc["doc_id"], doc["acl"], doc["classification"])
            return "ACL"
        return "SAME"

    async def list_source_ids(self, source: str) -> dict[str, str]:
        """source_id -> updated_at, for everything in pilot scope."""
        if source == "confluence":
            c: Confluence = self.clients["confluence"]
            out = {}
            for sp in await c.spaces():
                for p in await c.list_page_ids(sp["id"]):
                    out[p["id"]] = (p.get("version") or {}).get("createdAt", "")
            return out
        j: Jira = self.clients["jira"]
        keys = [p["key"] for p in await j.projects()]
        if not keys:
            return {}
        issues = await j.search(f"project in ({','.join(keys)}) ORDER BY key", fields=["updated"], limit=100000)
        return {i["key"]: i["fields"].get("updated", "") for i in issues}
