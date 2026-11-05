"""Safety checks on what goes in and out of the model.

Plain English: a security guard at three doors.
  1. The question coming in   - blocks "ignore your instructions..." style attacks.
  2. Content pulled from Jira/Confluence - a page could contain hidden
     instructions aimed at the AI; such passages are dropped before the model sees them.
  3. The answer going out    - personal data (emails, phone numbers, card numbers)
     is masked.

The built-in checks are lightweight pattern rules so they run on a laptop.
In production, set KMS_GUARD_PRESIDIO=1 (Presidio PII detection) and put
NeMo Guardrails / LLM Guard / Granite Guardian in front via the same functions.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above) (instructions|prompts?|rules)",
    r"disregard (all |the )?(previous|prior|above|system)",
    r"you are now (?:a|an|the) ",
    r"(reveal|print|show|leak) (your|the) (system prompt|instructions|hidden)",
    r"pretend (to be|you are)",
    r"act as (?:a|an) (?:unfiltered|jailbroken|dan)",
    r"\bjailbreak\b",
    r"do anything now",
    r"<\s*/?\s*(system|assistant)\s*>",
    r"\bsystem prompt\b.*\b(override|replace)\b",
    r"(send|post|exfiltrate|upload) .{0,40}(to|at) https?://",
]
_INJ = re.compile("|".join(INJECTION_PATTERNS), re.I)

PII_PATTERNS = {
    "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "CARD": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "PHONE": re.compile(r"(?<!\w)(?:\+?\d{1,3}[ .-]?)?(?:\(\d{2,4}\)[ .-]?)?\d{3,4}[ .-]\d{3,4}(?:[ .-]\d{2,4})?(?!\w)"),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "IBAN": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
}


@dataclass
class GuardResult:
    allowed: bool
    reason: str = ""
    text: str = ""


def check_input(question: str) -> GuardResult:
    if not question or not question.strip():
        return GuardResult(False, "empty_question")
    if len(question) > 4000:
        return GuardResult(False, "question_too_long")
    if _INJ.search(question):
        return GuardResult(False, "prompt_injection_suspected")
    return GuardResult(True, text=question.strip())


def check_retrieved(text: str) -> GuardResult:
    """Retrieval-time suspicious-pattern filter."""
    m = _INJ.search(text or "")
    if m:
        return GuardResult(False, f"embedded_instruction:{m.group(0)[:40]}")
    return GuardResult(True, text=text)


def redact_pii(text: str) -> str:
    if os.getenv("KMS_GUARD_PRESIDIO") == "1":
        try:
            return _presidio_redact(text)
        except Exception:  # noqa: BLE001 - fall back to patterns
            pass
    for label, pat in PII_PATTERNS.items():
        text = pat.sub(f"[{label}]", text)
    return text


def _presidio_redact(text: str) -> str:
    from presidio_analyzer import AnalyzerEngine
    from presidio_anonymizer import AnonymizerEngine

    global _an, _anon
    try:
        _an, _anon
    except NameError:
        _an, _anon = AnalyzerEngine(), AnonymizerEngine()
    results = _an.analyze(text=text, language="en")
    return _anon.anonymize(text=text, analyzer_results=results).text
