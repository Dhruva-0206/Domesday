import pytest

from domesday.db.registry import AccessDenied, UnknownNamespace
from domesday.db.schema import (FULLTEXT_INDEXES, RANGE_INDEXES, VECTOR_INDEXES, ensure_indexes,
                                existing_indexes, seed_attributes)
from domesday.db.seeds import ATTRIBUTE_GROUPS

pytestmark = pytest.mark.integration


def test_schema_creates_every_index_once(make_ns):
    ns = make_ns()
    have = existing_indexes(ns)
    expected = ({(l, p, "RANGE") for l, p in RANGE_INDEXES} | {(l, p, "VECTOR") for l, p in VECTOR_INDEXES}
                | {(l, p, "FULLTEXT") for l, ps in FULLTEXT_INDEXES for p in ps})
    assert expected <= have
    assert ensure_indexes(ns) == 0  # idempotent


def test_vector_index_has_namespace_dimension(make_ns):
    ns = make_ns()
    rows = ns.ro_query("CALL db.indexes() YIELD label, options WHERE label = 'Episode' RETURN options")
    assert rows[0][0]["embedding"]["dimension"] == ns.embed_dim
    assert rows[0][0]["embedding"]["similarityFunction"] == "cosine"


def test_seeded_attribute_synonyms_resolve(make_ns):
    ns = make_ns()
    assert seed_attributes(ns) == len(ATTRIBUTE_GROUPS)  # MERGE: re-seeding adds nothing new
    rows = ns.ro_query("MATCH (:AttrAlias {key:$k})-[:ALIAS_OF]->(a:Attribute) RETURN a.key, a.cardinality",
                       {"k": "city"})
    assert rows == [["residence", "one"]]
    assert ns.ro_query("MATCH (a:Attribute) RETURN count(a)")[0][0] == len(ATTRIBUTE_GROUPS)


def test_membership_is_required(registry, make_ns):
    ns = make_ns(owner="alice")
    assert registry.open("alice", ns.kind, ns.ident).role == "owner"
    with pytest.raises(AccessDenied):
        registry.open("mallory", ns.kind, ns.ident)
    with pytest.raises(UnknownNamespace):
        registry.open("alice", "sandbox", "does-not-exist")


def test_reader_cannot_write_and_revoke_works(registry, make_ns):
    ns = make_ns(owner="alice")
    registry.grant("bob", ns.kind, ns.ident, "reader")
    bob = registry.open("bob", ns.kind, ns.ident)
    assert bob.ro_query("RETURN 1") == [[1]]
    with pytest.raises(PermissionError):
        bob.query("CREATE (:Episode {id:'x'})")
    with pytest.raises(PermissionError):
        bob.drop()
    registry.revoke("bob", ns.kind, ns.ident)
    with pytest.raises(AccessDenied):
        registry.open("bob", ns.kind, ns.ident)


def test_embedding_dimension_is_pinned(registry, make_ns, embedder):
    ns = make_ns(owner="alice")
    with pytest.raises(ValueError, match="reindex"):
        registry.create_namespace(ns.kind, ns.ident, embedder.model, embedder.dim + 1, owner="alice")
    with pytest.raises(ValueError, match="expects"):
        ns.check_vector([0.1] * (ns.embed_dim - 1))


def test_namespaces_for_lists_memberships(registry, make_ns):
    a, b = make_ns(owner="carol"), make_ns(owner="dave")
    registry.grant("carol", b.kind, b.ident, "writer")
    mine = {(row["name"], row["role"]) for row in registry.namespaces_for("carol")}
    assert {(a.name, "owner"), (b.name, "writer")} <= mine
