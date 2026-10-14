"""The structured output an extractor must return for one episode. Validated by pydantic (D23)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EntityKind = Literal["person", "organization", "place", "object", "concept", "event", "other"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class EntityMention(_Strict):
    name: str = Field(min_length=1, description="Surface form as written. The speaker is 'User'.")
    kind: EntityKind = "other"


class StateFact(_Strict):
    entity: str = Field(min_length=1, description="Entity the fact is about")
    attribute: str = Field(min_length=1, description="snake_case attribute; reuse known keys when they fit")
    value: str = Field(min_length=1)
    cardinality: Literal["one", "many"] = Field(
        "one", description="'one' if a new value replaces the old (residence), 'many' if values accumulate (likes)")
    valid_from: str | None = Field(None, description="ISO date when this became true, if stated")
    confidence: float = Field(0.8, ge=0.0, le=1.0)


class ActionFact(_Strict):
    actor: str = Field(min_length=1)
    description: str = Field(min_length=1)
    target: str | None = None


class RelationFact(_Strict):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    relation_type: str = Field(min_length=1, description="snake_case, e.g. sister_of, works_at, manages")
    description: str = ""


class RoleFact(_Strict):
    entity: str = Field(min_length=1)
    name: str = Field(min_length=1, description="Role held, e.g. 'manager', 'landlord'")
    description: str = ""


class LocationFact(_Strict):
    name: str = Field(min_length=1)
    place_type: str = "place"


class Extraction(_Strict):
    summary: str = Field(min_length=1)
    emotion: str = "neutral"
    importance: float = Field(0.3, ge=0.0, le=1.0)
    entities: list[EntityMention] = []
    states: list[StateFact] = []
    actions: list[ActionFact] = []
    relations: list[RelationFact] = []
    roles: list[RoleFact] = []
    location: LocationFact | None = None
    event_time: str | None = Field(None, description="ISO date or datetime when the event happened, if stated")
