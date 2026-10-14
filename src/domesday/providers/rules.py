"""Rule-based extraction for the offline fake provider.

Covers the sentence shapes the tests and demo use: residence, employer, job, age, name, likes,
family/work relations, "X's attr is v", "my attr is v", marriages, pets, and explicit dates.
It is deliberately small and predictable rather than clever.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from domesday.extract.models import (ActionFact, EntityMention, Extraction, LocationFact, RelationFact,
                                     StateFact)
from domesday.text import attr_key

USER = "User"
_NAME = r"[A-Z][\w.'\-]*(?:\s+(?:[A-Z][\w.'\-]*|of|de|da|van|von|del))*"
_SUBJ = rf"(?P<s>\bI\b|{_NAME})"
_NOT_NAMES = {"I", "I'm", "I've", "I'd", "I'll", "My", "The", "A", "An", "In", "On", "At", "Yesterday", "Today", "Last", "Next", "This",
              "We", "Our", "He", "She", "They", "It", "Then", "And", "But", "So", "After", "Before", "Since"}
_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                        "september", "october", "november", "december"], 1)}
_RELATIONS = ("sister|brother|wife|husband|partner|girlfriend|boyfriend|friend|boss|manager|mother|father|mom|"
              "dad|son|daughter|colleague|cousin|roommate|landlord|doctor|teacher|mentor|cofounder|co-founder")
_PETS = "dog|cat|puppy|kitten|parrot|rabbit|hamster|horse"

_RULES: list[tuple[str, str, str, str]] = [
    # (regex, attribute, value-kind, cardinality). Group 's' = subject, 'v' = value.
    (rf"{_SUBJ}\s+(?:now\s+)?(?:live|lives|living|reside|resides|am living|is living)\s+in\s+(?P<v>{_NAME})",
     "residence", "place", "one"),
    (rf"{_SUBJ}\s+(?:just\s+)?(?:moved|relocated|have moved|has moved)\s+(?:from\s+{_NAME}\s+)?(?:back\s+)?to\s+"
     rf"(?P<v>{_NAME})", "residence", "place", "one"),
    (rf"{_SUBJ}\s+(?:now\s+)?(?:work|works|working|am working|is working)\s+(?:at|for)\s+(?P<v>{_NAME})",
     "employer", "organization", "one"),
    (rf"{_SUBJ}\s+(?:just\s+)?(?:joined|started at|started working at|was hired by|got hired at)\s+(?P<v>{_NAME})",
     "employer", "organization", "one"),
    (rf"{_SUBJ}\s+(?:am|is|'m|was)\s+(?P<v>\d{{1,3}})(?:\s+years?\s+old)?\b", "age", "value", "one"),
    (r"(?P<s>\bI)(?:\s+am|'m)\s+(?:a|an)\s+(?P<v>[a-z][a-z \-]{2,40}?)(?=\s+(?:at|for|in)\b|[.,;!?]|$)",
     "occupation", "value", "one"),
    (rf"(?P<s>\bI)(?:\s+am|'m)\s+(?:a|an)\s+[a-z][a-z \-]{{2,40}}?\s+(?:at|for)\s+(?P<v>{_NAME})",
     "employer", "organization", "one"),
    (rf"(?P<s>{_NAME})\s+is\s+(?:a|an)\s+(?P<v>[a-z][a-z \-]{{2,40}}?)(?=\s+(?:at|for|in)\b|[.,;!?]|$)",
     "occupation", "value", "one"),
    (rf"{_SUBJ}\s+(?:really\s+)?(?:like|likes|love|loves|enjoy|enjoys)\s+(?P<v>[\w' \-]{{2,40}}?)(?=[.,;!?]|\s+and\b|$)",
     "likes", "value", "many"),
    (rf"{_SUBJ}\s+(?:got married to|married|is married to|am married to)\s+(?P<v>{_NAME})",
     "spouse", "person", "one"),
    (r"(?P<s>\bI\b)?[Mm]y name is (?P<v>" + _NAME + ")", "name", "name", "one"),
]
_GENERIC_POSSESSIVE = re.compile(
    rf"(?P<s>\b[Mm]y|{_NAME}'s)\s+(?P<a>[a-z][a-z ]{{1,30}}?)\s+(?:is|are|was)\s+(?:now\s+)?"
    rf"(?P<v>[\w@.'\- ]{{1,60}}?)(?=[.,;!?]|\s+and\b|$)")
_RELATION_RE = re.compile(rf"\b[Mm]y\s+(?P<r>{_RELATIONS})(?:,)?\s+(?:is\s+)?(?:named\s+|called\s+)?(?P<n>{_NAME})")
_RELATION_REV = re.compile(rf"(?P<n>{_NAME})\s+is\s+my\s+(?P<r>{_RELATIONS})\b")
_PET_RE = re.compile(rf"\b(?:I|[Ww]e)\s+(?:have|got|adopted)\s+a\s+(?P<k>{_PETS})\s+(?:named|called)\s+(?P<n>{_NAME})")
_EMOTIONS = {
    "joy": "happy|excited|thrilled|glad|delighted|love|loved|great|wonderful",
    "sadness": "sad|upset|miss|lonely|depressed|heartbroken|unhappy",
    "anger": "angry|furious|annoyed|frustrated|mad",
    "fear": "worried|anxious|scared|afraid|nervous|stressed",
}
_MILESTONES = re.compile(r"\b(moved|relocated|married|divorced|promoted|hired|joined|quit|graduated|engaged|"
                         r"retired|fired|bought a house|new job)\b", re.I)
_LANDMARKS = re.compile(r"\b(born|died|passed away|diagnosed|wedding|pregnant)\b", re.I)


def _clean_name(name: str | None) -> str | None:
    if not name:
        return None
    parts = re.sub(r"'s\b", "", name.strip().rstrip(".")).split()
    while parts and parts[0] in _NOT_NAMES:
        parts = parts[1:]
    while parts and parts[-1] in {"of", "de", "da", "van", "von", "del"}:
        parts = parts[:-1]
    return " ".join(parts) or None


def _subject(raw: str | None) -> str | None:
    if raw is None or raw.strip() in {"I", "My", "my"}:
        return USER
    if raw.endswith("'s"):
        raw = raw[:-2]
    return _clean_name(raw)


def parse_date(text: str, now: datetime) -> str | None:
    """Explicit dates only, plus 'yesterday'/'last year'. Returns an ISO date string."""
    if m := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        return m.group(0)
    if m := re.search(r"\b(" + "|".join(_MONTHS) + r")\s+(?:(\d{1,2})(?:st|nd|rd|th)?,?\s+)?(\d{4})\b", text, re.I):
        return f"{int(m.group(3)):04d}-{_MONTHS[m.group(1).lower()]:02d}-{int(m.group(2) or 1):02d}"
    if m := re.search(r"\b(?:in|since|during)\s+(\d{4})\b", text):
        return f"{m.group(1)}-01-01"
    if re.search(r"\byesterday\b", text, re.I):
        return (now - timedelta(days=1)).date().isoformat()
    if re.search(r"\blast year\b", text, re.I):
        return f"{now.year - 1}-01-01"
    return None


def extract_rules(text: str, now_ms: int | None = None) -> Extraction:
    now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc) if now_ms else datetime.now(timezone.utc)
    entities: dict[str, str] = {}
    states: list[StateFact] = []
    relations: list[RelationFact] = []
    actions: list[ActionFact] = []
    location: LocationFact | None = None
    event_time = parse_date(text, now)

    def mention(name: str, kind: str) -> None:
        if name and (name not in entities or entities[name] == "other"):
            entities[name] = kind

    if re.search(r"\b(I|me|my|myself|I'm)\b", text, re.I):
        mention(USER, "person")

    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        for pattern, attribute, kind, cardinality in _RULES:
            for m in re.finditer(pattern, sentence):
                subject = _subject(m.group("s"))
                value = (_clean_name(m.group("v")) if kind in {"place", "organization", "person", "name"}
                         else m.group("v").strip().rstrip(".!?"))
                if not subject or not value:
                    continue
                mention(subject, "person")
                if kind in {"place", "organization", "person"}:
                    mention(value, kind)
                if kind == "place" and location is None:
                    location = LocationFact(name=value, place_type="city")
                if kind == "organization":
                    relations.append(RelationFact(source=subject, target=value, relation_type="works_at",
                                                  description=f"{subject} works at {value}"))
                states.append(StateFact(entity=subject, attribute=attribute, value=value,
                                        cardinality=cardinality, valid_from=event_time))
        for m in _GENERIC_POSSESSIVE.finditer(sentence):
            subject, attribute = _subject(m.group("s")), attr_key(m.group("a"))
            if subject and attribute and attribute not in {"name"} | set(_RELATIONS.split("|")):
                mention(subject, "person")
                states.append(StateFact(entity=subject, attribute=attribute, value=m.group("v").strip(),
                                        valid_from=event_time))
        for rx in (_RELATION_RE, _RELATION_REV):
            for m in rx.finditer(sentence):
                if person := _clean_name(m.group("n")):
                    mention(person, "person")
                    mention(USER, "person")
                    rel = m.group("r").lower().replace("-", "")
                    relations.append(RelationFact(source=person, target=USER, relation_type=f"{rel}_of",
                                                  description=f"{person} is the user's {rel}"))
        for m in _PET_RE.finditer(sentence):
            if pet := _clean_name(m.group("n")):
                mention(USER, "person")
                mention(pet, "object")
                states.append(StateFact(entity=USER, attribute="pets", value=f"{pet} ({m.group('k')})",
                                        cardinality="many", valid_from=event_time))
        if _MILESTONES.search(sentence):
            actions.append(ActionFact(actor=USER if USER in entities else next(iter(entities), USER),
                                      description=sentence.strip()[:200], target=None))

    for name in re.findall(_NAME, text):
        if (clean := _clean_name(name)) and clean not in _NOT_NAMES and clean.lower() not in _MONTHS:
            mention(clean, "other")

    importance = 0.3
    if states or relations:
        importance = 0.5
    if _MILESTONES.search(text):
        importance = 0.8
    if _LANDMARKS.search(text):
        importance = 0.9
    emotion = next((e for e, pat in _EMOTIONS.items() if re.search(rf"\b({pat})\b", text, re.I)), "neutral")
    summary = re.split(r"(?<=[.!?])\s+", text.strip())[0][:240] or text[:240]
    return Extraction(
        summary=summary, emotion=emotion, importance=importance,
        entities=[EntityMention(name=n, kind=k) for n, k in entities.items()],
        states=states, actions=actions, relations=relations, roles=[], location=location,
        event_time=event_time,
    )
