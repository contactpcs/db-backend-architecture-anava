"""Text helpers for the assistant: normalising, FAQ matching, redaction before
anything leaves for the AI provider, and the check on what comes back.

No external libraries and no database extension: the FAQ is a few hundred
short rows, so a stdlib similarity score over them is fast enough.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Below this the FAQ answer is not used and the message goes to the AI step
# (or the fallback). Tuned on the brief's question bank; raise it if wrong
# answers appear, lower it if obvious matches fall through.
MATCH_THRESHOLD = 0.54

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")
_STOP = {
    "en": {
        "a",
        "an",
        "the",
        "is",
        "are",
        "am",
        "i",
        "my",
        "me",
        "do",
        "does",
        "can",
        "to",
        "of",
        "for",
        "in",
        "on",
        "it",
        "please",
        "what",
        "how",
    },
    "hi": {"है", "हैं", "का", "की", "के", "को", "में", "मेरा", "मेरी", "मेरे", "क्या", "कैसे", "मैं", "मुझे", "कृपया"},
    "mr": {"आहे", "आहेत", "का", "चा", "ची", "चे", "मला", "माझा", "माझी", "माझं", "मी", "कसे", "कसं", "काय", "कृपया"},
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").lower()
    return _SPACES.sub(" ", _PUNCT.sub(" ", text)).strip()


def _tokens(text: str, language: str) -> set[str]:
    stop = _STOP.get(language, set()) | _STOP["en"]
    return {t for t in text.split() if t not in stop}


def similarity(a: str, b: str, language: str) -> float:
    """0..1. Half character similarity (catches typos and word order), half
    word overlap (catches extra words around the same question)."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    chars = SequenceMatcher(None, na, nb).ratio()
    ta, tb = _tokens(na, language), _tokens(nb, language)
    words = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    return 0.5 * chars + 0.5 * words


def rank_faq(message: str, rows: list[dict], language: str) -> list[tuple[float, dict]]:
    """Each FAQ row scored by its best-matching example question, best first."""
    scored = [(max(similarity(message, q, language) for q in row["questions"]), row) for row in rows if row.get("questions")]
    return sorted(scored, key=lambda pair: pair[0], reverse=True)


# -- leaving our system ---------------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE)
_LONG_NUMBER = re.compile(r"(?:\+?\d[\d\s-]{6,}\d)")


def redact(text: str, names: list[str] | None = None) -> str:
    """Strip what could identify the patient before text goes to the AI
    provider: emails, IDs, phone-like numbers and the patient's own names."""
    text = _EMAIL.sub("[email]", text)
    text = _UUID.sub("[id]", text)
    text = _LONG_NUMBER.sub("[number]", text)
    for name in names or []:
        if name and len(name) > 1:
            text = re.sub(rf"\b{re.escape(name)}\b", "[name]", text, flags=re.IGNORECASE)
    return text


# -- coming back -----------------------------------------------------------------

# Anything that reads like medical advice is replaced by the fallback reply.
_BLOCKED = re.compile(
    r"\b(\d+\s?(mg|mcg|ml|milligram)s?|dosage|dose|tablet|capsule|prescri\w*|diagnos\w*|you (have|may have|might have|probably have)|"
    r"stop taking|start taking|increase your|decrease your|painkiller|paracetamol|ibuprofen|antidepressant)\b"
    r"|खुराक|दवा लें|गोली|दवा बंद|डोस|औषध घ्या|गोळी",
    re.IGNORECASE,
)


def output_ok(text: str | None) -> bool:
    if not text or len(text) > 1200:
        return False
    return not _BLOCKED.search(text)


def fill(template: str, values: dict[str, str | None]) -> str:
    """Replace {placeholders} we know. Unknown ones stay visible so a reviewer
    spots a FAQ answer that asks for data this code doesn't supply."""

    def repl(m: re.Match) -> str:
        value = values.get(m.group(1))
        return value if value else m.group(0)

    return re.sub(r"\{(\w+)\}", repl, template)
