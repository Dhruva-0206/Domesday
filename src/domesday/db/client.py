"""FalkorDB connection pool and the Namespace handle every operation goes through.

Application code never selects a graph by raw name (D4). It asks the registry for a
Namespace, which carries its graph, its embedding model and dimension, and the
per-query timeout. One Database per process is shared by all threads (E13).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import redis
from falkordb import FalkorDB
from falkordb.graph import Graph

from domesday.config import Settings, load_settings

NAMESPACE_KINDS = ("user", "team", "project", "sandbox")
REGISTRY_GRAPH = "dd:registry"
_IDENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")


def graph_name(kind: str, ident: str) -> str:
    """The graph name for a namespace: dd:{kind}:{id}. Validated so ids can't smuggle separators."""
    if kind not in NAMESPACE_KINDS:
        raise ValueError(f"namespace kind must be one of {NAMESPACE_KINDS}, got {kind!r}")
    if not _IDENT_RE.match(ident):
        raise ValueError(f"invalid namespace id {ident!r}: use letters, digits, '_', '.', '-' (max 64)")
    return f"dd:{kind}:{ident}"


class Database:
    """One pooled FalkorDB client. Blocking pool: callers wait for a free connection instead of failing."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        s = self.settings
        pool = redis.BlockingConnectionPool(
            host=s.falkordb_host, port=s.falkordb_port,
            username=s.falkordb_username, password=s.falkordb_password,
            max_connections=s.falkordb_max_connections, timeout=30, decode_responses=True,
        )
        self._db = FalkorDB(connection_pool=pool)

    def _graph(self, name: str) -> Graph:
        return self._db.select_graph(name)

    def registry_graph(self) -> Graph:
        return self._graph(REGISTRY_GRAPH)

    def ping(self) -> bool:
        return bool(self._db.connection.ping())

    def config_get(self, key: str) -> Any:
        return self._db.config_get(key)

    def list_graphs(self) -> list[str]:
        return list(self._db.list_graphs())

    def open_namespace(self, kind: str, ident: str, embed_model: str, embed_dim: int,
                       role: str = "owner") -> "Namespace":
        """Build a handle. Called only by the registry, after it has resolved and authorised the namespace."""
        name = graph_name(kind, ident)
        return Namespace(name=name, kind=kind, ident=ident, embed_model=embed_model, embed_dim=embed_dim,
                         role=role, timeout_ms=self.settings.query_timeout_ms, graph=self._graph(name))


@dataclass(frozen=True)
class Namespace:
    """A resolved, authorised tenant graph. All Cypher for that tenant runs through here."""

    name: str
    kind: str
    ident: str
    embed_model: str
    embed_dim: int
    role: str
    timeout_ms: int
    graph: Graph = field(repr=False, compare=False)

    @property
    def writable(self) -> bool:
        return self.role in ("owner", "writer")

    def require_write(self) -> None:
        if not self.writable:
            raise PermissionError(f"{self.name}: role {self.role!r} is read-only")

    def query(self, cypher: str, params: dict | None = None) -> list[list]:
        self.require_write()
        return self.graph.query(cypher, params or {}, timeout=self.timeout_ms).result_set

    def ro_query(self, cypher: str, params: dict | None = None) -> list[list]:
        return self.graph.ro_query(cypher, params or {}, timeout=self.timeout_ms).result_set

    def rows(self, cypher: str, params: dict | None = None, read_only: bool = True) -> list[dict]:
        """Run a query and return each row as a dict keyed by the RETURN aliases."""
        if not read_only:
            self.require_write()
        run = self.graph.ro_query if read_only else self.graph.query
        res = run(cypher, params or {}, timeout=self.timeout_ms)
        cols = [h[1] for h in res.header]
        return [dict(zip(cols, row)) for row in res.result_set]

    def check_vector(self, vec: list[float]) -> list[float]:
        """FalkorDB silently accepts wrong-dimension vectors and they become unsearchable (E4, D16)."""
        if len(vec) != self.embed_dim:
            raise ValueError(f"{self.name}: vector has dim {len(vec)}, namespace expects {self.embed_dim}")
        return vec

    def drop(self) -> None:
        """Delete the whole graph. Used by clear_memory and tests."""
        if self.role != "owner":
            raise PermissionError(f"{self.name}: only an owner can drop the namespace")
        try:
            self.graph.delete()
        except redis.ResponseError as exc:  # graph never created
            if "empty key" not in str(exc).lower() and "invalid graph" not in str(exc).lower():
                raise
