"""Small text helpers shared by the write and read paths: key normalisation and tokenising."""
from __future__ import annotations

import re
import unicodedata

STOPWORDS = frozenset("""
a an and are as at be been being but by can could did do does doing for from had has have having he her
here hers him his how i if in into is it its me my myself no nor not of off on once only or our ours
out over own she should so some such than that the their theirs them then there these they this those
through to too under until up very was we were what when where which while who whom why will with would
you your yours yourself now currently ever still just about tell know
""".split())

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def fold(text: str) -> str:
    """Lowercase and strip accents so 'Zürich' and 'zurich' compare equal."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def entity_key(name: str) -> str:
    """Canonical entity key: folded, punctuation removed, single spaces. 'The  Acme, Inc.' -> 'the acme inc'."""
    return " ".join(re.findall(r"[a-z0-9]+", fold(name)))


def attr_key(name: str) -> str:
    """Canonical attribute key in snake_case. 'Lives In' -> 'lives_in'."""
    return "_".join(re.findall(r"[a-z0-9]+", fold(name)))


def words(text: str) -> list[str]:
    return _WORD_RE.findall(fold(text))


def content_tokens(text: str) -> list[str]:
    """Words minus stopwords, in order, de-duplicated. Used to build fulltext OR queries (E7)."""
    seen: dict[str, None] = {}
    for w in words(text):
        w = w.split("'")[0]
        if len(w) > 1 and w not in STOPWORDS:
            seen.setdefault(w, None)
    return list(seen)
