"""The dd:registry graph: which namespaces exist, their embedding config, and which agents may open them.

    (:Namespace {name, kind, ident, created_at, embed_model, embed_dim, schema_version})
    (:Agent {id, name, created_at, last_seen})-[:MEMBER_OF {role}]->(:Namespace)

`open()` is the only way to get a Namespace handle, and it checks MEMBER_OF first (D4).
"""
from __future__ import annotations

import redis

from domesday.db.client import Database, Namespace, graph_name
from domesday.db.schema import SCHEMA_VERSION, ensure_schema

ROLES = ("owner", "writer", "reader")

ENSURE_NAMESPACE = """
MERGE (n:Namespace {name: $name})
  ON CREATE SET n.kind = $kind, n.ident = $ident, n.created_at = timestamp(),
                n.embed_model = $model, n.embed_dim = $dim, n.schema_version = $schema_version
RETURN n.embed_model, n.embed_dim
"""
ENSURE_AGENT = """
MERGE (a:Agent {id: $id})
  ON CREATE SET a.created_at = timestamp(), a.name = $name
SET a.last_seen = timestamp()
RETURN a.id
"""
GRANT = """
MATCH (a:Agent {id: $agent}), (n:Namespace {name: $name})
MERGE (a)-[m:MEMBER_OF]->(n)
SET m.role = $role
RETURN m.role
"""
REVOKE = "MATCH (:Agent {id: $agent})-[m:MEMBER_OF]->(:Namespace {name: $name}) DELETE m"
RESOLVE = """
MATCH (n:Namespace {name: $name})
OPTIONAL MATCH (:Agent {id: $agent})-[m:MEMBER_OF]->(n)
RETURN n.kind, n.ident, n.embed_model, n.embed_dim, m.role
"""
LIST_FOR_AGENT = """
MATCH (:Agent {id: $agent})-[m:MEMBER_OF]->(n:Namespace)
RETURN n.name AS name, n.kind AS kind, n.ident AS ident, m.role AS role, n.embed_model AS embed_model,
       n.embed_dim AS embed_dim
ORDER BY name
"""
SET_EMBEDDING = "MATCH (n:Namespace {name: $name}) SET n.embed_model = $model, n.embed_dim = $dim RETURN n.name"
DELETE_NAMESPACE = "MATCH (n:Namespace {name: $name}) DETACH DELETE n"


class AccessDenied(PermissionError):
    pass


class UnknownNamespace(KeyError):
    pass


class Registry:
    def __init__(self, db: Database):
        self.db = db
        self.graph = db.registry_graph()
        self._timeout = db.settings.query_timeout_ms
        self._ensure_indexes()

    def _q(self, cypher: str, params: dict | None = None) -> list[list]:
        return self.graph.query(cypher, params or {}, timeout=self._timeout).result_set

    def _ensure_indexes(self) -> None:
        for label, prop in (("Namespace", "name"), ("Agent", "id")):
            try:
                self._q(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
            except redis.ResponseError as exc:
                if "already indexed" not in str(exc):
                    raise

    def register_agent(self, agent_id: str, name: str | None = None) -> str:
        return self._q(ENSURE_AGENT, {"id": agent_id, "name": name or agent_id})[0][0]

    def create_namespace(self, kind: str, ident: str, embed_model: str, embed_dim: int,
                         owner: str | None = None) -> Namespace:
        """Create (or re-open) a namespace, build its indexes, and make `owner` its owner.

        Re-creating with a different embedding model or dimension is refused: vectors of another
        dimension would be stored silently and never found (E4).
        """
        name = graph_name(kind, ident)
        model, dim = self._q(ENSURE_NAMESPACE, {"name": name, "kind": kind, "ident": ident, "model": embed_model,
                                                "dim": int(embed_dim), "schema_version": SCHEMA_VERSION})[0]
        if (model, dim) != (embed_model, int(embed_dim)):
            raise ValueError(f"{name} uses embeddings {model} ({dim}-d), not {embed_model} ({embed_dim}-d); "
                             "run reindex to migrate")
        if owner is not None:
            self.register_agent(owner)
            self.grant(owner, kind, ident, "owner")
        ns = self.db.open_namespace(kind, ident, model, dim, role="owner")
        ensure_schema(ns)
        return ns

    def grant(self, agent_id: str, kind: str, ident: str, role: str = "writer") -> None:
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        self.register_agent(agent_id)
        if not self._q(GRANT, {"agent": agent_id, "name": graph_name(kind, ident), "role": role}):
            raise UnknownNamespace(graph_name(kind, ident))

    def revoke(self, agent_id: str, kind: str, ident: str) -> None:
        self._q(REVOKE, {"agent": agent_id, "name": graph_name(kind, ident)})

    def role_of(self, agent_id: str, kind: str, ident: str) -> str | None:
        rows = self._q(RESOLVE, {"agent": agent_id, "name": graph_name(kind, ident)})
        return rows[0][4] if rows else None

    def open(self, agent_id: str, kind: str, ident: str) -> Namespace:
        """Resolve a namespace for an agent. Raises UnknownNamespace or AccessDenied."""
        name = graph_name(kind, ident)
        rows = self._q(RESOLVE, {"agent": agent_id, "name": name})
        if not rows:
            raise UnknownNamespace(name)
        _kind, _ident, model, dim, role = rows[0]
        if role is None:
            raise AccessDenied(f"agent {agent_id!r} is not a member of {name}")
        return self.db.open_namespace(kind, ident, model, dim, role=role)

    def namespaces_for(self, agent_id: str) -> list[dict]:
        res = self.graph.query(LIST_FOR_AGENT, {"agent": agent_id}, timeout=self._timeout)
        cols = [h[1] for h in res.header]
        return [dict(zip(cols, row)) for row in res.result_set]

    def set_embedding(self, kind: str, ident: str, embed_model: str, embed_dim: int) -> None:
        """Record a new embedding model after a reindex has rewritten every vector."""
        self._q(SET_EMBEDDING, {"name": graph_name(kind, ident), "model": embed_model, "dim": int(embed_dim)})

    def delete_namespace(self, kind: str, ident: str) -> None:
        """Drop the tenant graph and its registry entry (memberships go with it)."""
        self.db.open_namespace(kind, ident, "", 0).drop()
        self._q(DELETE_NAMESPACE, {"name": graph_name(kind, ident)})
