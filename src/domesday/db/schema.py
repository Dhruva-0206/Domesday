"""Per-namespace indexes and seeded attribute vocabulary.

Index DDL forms are the ones verified live: DDL `CREATE FULLTEXT INDEX` (the procedure form takes one
argument in 6.0, E6), cosine vector indexes, and range indexes on keys and epoch-ms times (E15).
Re-creating an index raises "already indexed", so we diff against db.indexes() and tolerate that
error when two processes initialise the same graph at once.
"""
from __future__ import annotations

import redis

from domesday.db.client import Namespace
from domesday.db.seeds import ATTRIBUTE_GROUPS

SCHEMA_VERSION = 1

RANGE_INDEXES = [
    ("Episode", "id"), ("Episode", "ts"), ("Episode", "event_ts"), ("Episode", "status"),
    ("Entity", "key"), ("Alias", "key"), ("Attribute", "key"), ("AttrAlias", "key"),
    ("State", "id"), ("State", "attribute"), ("Location", "key"),
    ("Task", "id"), ("Task", "status"), ("Session", "id"), ("Turn", "id"),
    ("Procedure", "id"), ("Run", "id"), ("Handoff", "id"), ("Agent", "id"),
]
VECTOR_INDEXES = [("Episode", "embedding"), ("Entity", "embedding"), ("Attribute", "embedding"),
                  ("Procedure", "goal_embedding")]
FULLTEXT_INDEXES = [("Episode", ("raw_text", "summary", "searchable_text")), ("Procedure", ("name", "goal"))]

LIST_INDEXES = "CALL db.indexes() YIELD label, properties, types RETURN label, properties, types"

SEED_ATTRIBUTES = """
UNWIND $groups AS g
MERGE (a:Attribute {key: g.key})
  ON CREATE SET a.cardinality = g.cardinality, a.description = g.description, a.seeded = true,
                a.created_at = timestamp()
WITH a, g
UNWIND g.aliases AS alias
MERGE (x:AttrAlias {key: alias})
MERGE (x)-[:ALIAS_OF]->(a)
RETURN count(DISTINCT a)
"""


def existing_indexes(ns: Namespace) -> set[tuple[str, str, str]]:
    """{(label, property, TYPE)} for every index on the namespace graph (empty if the graph doesn't exist yet)."""
    try:
        rows = ns.ro_query(LIST_INDEXES)
    except redis.ResponseError as exc:
        if "empty key" in str(exc):
            return set()
        raise
    found = set()
    for label, _props, types in rows:
        for prop, kinds in types.items():
            found.update((label, prop, kind) for kind in kinds)
    return found


def _create(ns: Namespace, ddl: str) -> bool:
    try:
        ns.query(ddl)
        return True
    except redis.ResponseError as exc:
        if "already indexed" in str(exc):
            return False
        raise


def ensure_indexes(ns: Namespace) -> int:
    """Create any missing index. Returns how many DDL statements ran."""
    have = existing_indexes(ns)
    created = 0
    for label, prop in RANGE_INDEXES:
        if (label, prop, "RANGE") not in have:
            created += _create(ns, f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
    for label, prop in VECTOR_INDEXES:
        if (label, prop, "VECTOR") not in have:
            created += _create(ns, f"CREATE VECTOR INDEX FOR (n:{label}) ON (n.{prop}) "
                                   f"OPTIONS {{dimension: {int(ns.embed_dim)}, similarityFunction: 'cosine'}}")
    for label, props in FULLTEXT_INDEXES:
        if any((label, p, "FULLTEXT") not in have for p in props):
            fields = ", ".join(f"n.{p}" for p in props)
            created += _create(ns, f"CREATE FULLTEXT INDEX FOR (n:{label}) ON ({fields})")
    return created


def seed_attributes(ns: Namespace) -> int:
    groups = [{"key": k, "cardinality": c, "description": d, "aliases": aliases}
              for k, c, d, aliases in ATTRIBUTE_GROUPS]
    return ns.query(SEED_ATTRIBUTES, {"groups": groups})[0][0]


def ensure_schema(ns: Namespace) -> None:
    ensure_indexes(ns)
    seed_attributes(ns)
