"""Keyword (sparse) vectors for hybrid search.

Plain English: besides "meaning" search (dense vectors), we also want exact
word matches - ticket keys like PAY-142, error codes, product names. Each word
is hashed to a number; Qdrant applies IDF weighting server-side so rare words
count more (BM25-style).
"""
from __future__ import annotations

import re
import zlib
from collections import Counter

_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-\.]*")
_STOP = set("""a an and are as at be but by for from has have how i if in into is it its of on or our so that the
their then there these this to was we were what when where which who why will with you your do does can""".split())

K1, B, AVG_LEN = 1.2, 0.75, 250.0


def stem(t: str) -> str:
    """Very light suffix stripping so 'deploying' matches 'deploy' (keeps ticket keys intact)."""
    if any(c.isdigit() for c in t) or len(t) <= 4:
        return t
    for suf in ("ing", "ied", "ies", "ed", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            t = t[: -len(suf)] + ("y" if suf in ("ied", "ies") else "")
            break
    return t


def tokenize(text: str) -> list[str]:
    toks = []
    for t in _TOKEN.findall(text.lower()):
        t = t.strip(".-_")
        if not t or t in _STOP:
            continue
        t = stem(t)
        toks.append(t)
        if "-" in t:  # PAY-142 also matches "pay" and "142"
            toks.extend(p for p in t.split("-") if p and p not in _STOP)
    return toks


def _idx(tok: str) -> int:
    return zlib.crc32(tok.encode()) & 0x7FFFFFFF


def doc_vector(text: str) -> tuple[list[int], list[float]]:
    toks = tokenize(text)
    tf = Counter(_idx(t) for t in toks)
    n = len(toks) or 1
    idx, vals = [], []
    for i, f in tf.items():
        idx.append(i)
        vals.append(f * (K1 + 1) / (f + K1 * (1 - B + B * n / AVG_LEN)))
    return idx, vals


def query_vector(text: str) -> tuple[list[int], list[float]]:
    ids = sorted({_idx(t) for t in tokenize(text)})
    return ids, [1.0] * len(ids)
