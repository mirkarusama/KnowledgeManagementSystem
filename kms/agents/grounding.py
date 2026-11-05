"""Groundedness check: is every sentence of the answer backed by a cited source?

Plain English: a fact-checker reads the draft answer. Each sentence must point
to a source ([S1], [S2]...) and share enough of its wording with that source.
Sentences that cite nothing are removed. If too little survives, the user gets
an honest "I can't answer this confidently" instead of a guess.
"""
from __future__ import annotations

import re

from kms.index.sparse import tokenize

CITE = re.compile(r"\[S(\d+)\]")
_SENT = re.compile(r"(?<=[.!?\]])\s+(?=[A-Z0-9*\-])|\n+")  # citations stay with the sentence before them
SUPPORT_MIN = 0.4


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text or "") if s and s.strip()]


def check(answer: str, evidence: list[dict], strict: bool = True) -> dict:
    sources = {i + 1: e for i, e in enumerate(evidence)}
    kept, supported, cited_ids = [], 0, set()
    for sent in split_sentences(answer):
        ids = {int(n) for n in CITE.findall(sent)} & set(sources)
        bare = CITE.sub("", sent).strip()
        words = set(tokenize(bare))
        if not ids:
            # headings / list intros without facts (very short) are harmless; facts without citations are cut
            if strict and len(words) > 3:
                continue
            kept.append(sent)
            continue
        best = 0.0
        for i in ids:
            src = set(tokenize(f"{sources[i].get('title', '')} {sources[i].get('heading', '')} {sources[i].get('text', '')}"))
            if words:
                best = max(best, len(words & src) / len(words))
        if not words:
            continue  # a bare "[S1]" with no statement
        if best >= SUPPORT_MIN:
            supported += 1
            cited_ids |= ids
            kept.append(sent)
        elif not strict:
            kept.append(sent)
    factual = [s for s in kept if CITE.search(s)]
    score = supported / len(factual) if factual else 0.0
    return {"answer": "\n".join(_rejoin(kept)).strip(), "groundedness": round(score, 3),
            "cited": sorted(cited_ids), "sentences_kept": len(kept)}


def _rejoin(sents: list[str]) -> list[str]:
    """Keep bullet lines on their own line, join prose sentences with spaces."""
    out: list[str] = []
    for s in sents:
        if s.startswith(("-", "*", "•")) or re.match(r"^\d+[.)]\s", s) or not out:
            out.append(s)
        else:
            out[-1] = f"{out[-1]} {s}"
    return out


def confidence(groundedness: float, n_cited: int, threshold: float) -> str:
    if groundedness >= 0.85 and n_cited >= 2:
        return "High"
    if groundedness >= threshold and n_cited >= 1:
        return "Medium"
    return "Low"
