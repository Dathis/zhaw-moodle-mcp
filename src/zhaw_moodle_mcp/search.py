"""Local search over the course index: accent/umlaut-insensitive, all query
terms must match (exactly, as prefix/substring, or approximately)."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from difflib import SequenceMatcher
from typing import Any

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_WORD_RE = re.compile(r"[a-z0-9]+")

# ranking bonus per kind; files and modules are usually what people look for
_KIND_WEIGHT = {"module": 1.0, "file": 1.0, "section": 0.8, "course": 0.9}


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


def normalize_variants(text: str) -> set[str]:
    """Both spellings of umlauts: 'Übung' -> {'ubung', 'uebung'}."""
    lowered = text.casefold()
    return {_fold(lowered), _fold(lowered.translate(_UMLAUTS))}


def words(text: str) -> list[str]:
    result: list[str] = []
    for variant in normalize_variants(text):
        result.extend(_WORD_RE.findall(variant))
    return result


def _term_score(term: str, name_words: list[str], other_words: list[str]) -> float:
    """1.0 exact word, 0.8 prefix, 0.6 substring, up to 0.5 approximate; x0.6 if only in context."""
    best = 0.0
    for weight, pool in ((1.0, name_words), (0.6, other_words)):
        for word in pool:
            if word == term:
                score = 1.0
            elif word.startswith(term):
                score = 0.8
            elif len(term) >= 3 and term in word:
                score = 0.6
            elif len(term) >= 4 and abs(len(word) - len(term)) <= 2:
                ratio = SequenceMatcher(None, term, word).ratio()
                score = 0.5 if ratio >= 0.84 else 0.0
            else:
                score = 0.0
            best = max(best, score * weight)
            if best == 1.0:
                return best
    return best


def search(query: str, rows: Iterable[Mapping[str, Any]], limit: int = 25,
           kinds: set[str] | None = None) -> list[tuple[float, Mapping[str, Any]]]:
    # each query word may match in either umlaut spelling ('Übung' -> ubung / uebung);
    # umlaut transliteration never changes word boundaries, so the lists align
    plain = _WORD_RE.findall(_fold(query.casefold()))
    transliterated = _WORD_RE.findall(_fold(query.casefold().translate(_UMLAUTS)))
    groups = [{a, b} for a, b in zip(plain, transliterated, strict=True)]
    if not groups:
        return []

    scored = []
    for row in rows:
        if kinds and row["kind"] not in kinds:
            continue
        name_words = words(row["name"] or "")
        other_words = words(" ".join(str(row[k] or "") for k in ("section", "course_name", "type")))
        total = 0.0
        for group in groups:
            best = max(_term_score(t, name_words, other_words) for t in group)
            if best == 0.0:
                break
            total += best
        else:
            score = total / len(groups) * _KIND_WEIGHT.get(row["kind"], 1.0)
            scored.append((round(score, 3), row))
    scored.sort(key=lambda item: (-item[0], item[1]["kind"] != "file", item[1]["name"] or ""))
    return scored[:limit]
