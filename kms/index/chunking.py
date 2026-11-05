"""Turns pages and tickets into bite-sized passages ("chunks") for the index.

Plain English: a 20-page document is cut into passages of a few paragraphs,
each remembering which heading it sits under, so search can point to the
exact part that answers a question.

Docling (layout-aware parsing of PDFs/Office/HTML) is used when installed and
KMS_USE_DOCLING=true; otherwise a lightweight built-in HTML parser is used,
which is plenty for Confluence pages and keeps the laptop install small.
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser

from kms.config import get_settings


@dataclass
class Section:
    heading: str
    text: str


class _HTMLSections(HTMLParser):
    HEADINGS = {"h1", "h2", "h3", "h4"}
    BLOCKS = {"p", "li", "tr", "br", "div", "pre", "blockquote", "table"}

    def __init__(self):
        super().__init__()
        self.sections: list[Section] = [Section("", "")]
        self._in_heading = False
        self._heading_buf: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self.HEADINGS:
            self._in_heading = True
            self._heading_buf = []
        elif tag == "li":
            self.sections[-1].text += "\n- "
        elif tag in ("td", "th"):
            self.sections[-1].text += " | "

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.HEADINGS:
            self._in_heading = False
            self.sections.append(Section(" ".join("".join(self._heading_buf).split()), ""))
        elif tag in self.BLOCKS:
            self.sections[-1].text += "\n"

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_heading:
            self._heading_buf.append(data)
        else:
            self.sections[-1].text += data


def html_sections(html: str) -> list[Section]:
    s = get_settings()
    if s.use_docling:
        try:
            return _docling_sections(html)
        except Exception:  # noqa: BLE001 - fall back to the built-in parser
            pass
    p = _HTMLSections()
    p.feed(html or "")
    out = []
    for sec in p.sections:
        text = "\n".join(line.strip() for line in sec.text.splitlines() if line.strip())
        if text or sec.heading:
            out.append(Section(sec.heading, text))
    return out


def _docling_sections(html: str) -> list[Section]:
    from io import BytesIO

    from docling.datamodel.base_models import DocumentStream
    from docling.document_converter import DocumentConverter

    doc = DocumentConverter().convert(DocumentStream(name="page.html", stream=BytesIO(html.encode()))).document
    md = doc.export_to_markdown()
    sections, cur = [], Section("", "")
    for line in md.splitlines():
        if line.startswith("#"):
            sections.append(cur)
            cur = Section(line.lstrip("#").strip(), "")
        else:
            cur.text += line + "\n"
    sections.append(cur)
    return [s for s in sections if s.text.strip() or s.heading]


def adf_text(node) -> str:
    """Flatten Atlassian Document Format (Jira Cloud rich text) to plain text."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_text(n) for n in node)
    t = node.get("type")
    if t == "text":
        return node.get("text", "")
    inner = adf_text(node.get("content", []))
    if t in ("paragraph", "heading", "listItem", "codeBlock", "blockquote", "tableRow"):
        return inner + "\n"
    if t == "hardBreak":
        return "\n"
    return inner


def chunk_sections(title: str, sections: list[Section]) -> list[dict]:
    """Greedy packing of sections into ~chunk_chars passages, never mixing far-apart headings."""
    s = get_settings()
    size, overlap = s.chunk_chars, s.chunk_overlap
    chunks: list[dict] = []
    for sec in sections:
        body = sec.text.strip()
        if not body:
            continue
        start = 0
        while start < len(body):
            end = min(len(body), start + size)
            if end < len(body):  # try to cut at a paragraph or sentence boundary
                cut = max(body.rfind("\n", start, end), body.rfind(". ", start, end))
                if cut > start + size // 2:
                    end = cut + 1
            piece = body[start:end].strip()
            if piece:
                chunks.append({"heading": sec.heading, "text": piece})
            if end >= len(body):
                break
            start = max(end - overlap, start + 1)
    if not chunks:
        chunks.append({"heading": "", "text": title})
    for i, c in enumerate(chunks):
        prefix = f"{title}" + (f" > {c['heading']}" if c["heading"] else "")
        c["index"] = i
        c["embed_text"] = f"{prefix}\n{c['text']}"
    return chunks
