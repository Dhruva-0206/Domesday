"""Two tenants hold identical entity names with contradictory facts. Nothing written in one may be
visible from the other through any index or traversal. Later phases extend this to every public
operation of the Memory facade."""
import pytest

pytestmark = pytest.mark.integration

WRITE_TENANT = """
MERGE (e:Entity {key:'user'}) ON CREATE SET e.name = 'User', e.embedding = vecf32($ent_vec)
CREATE (ep:Episode {id:$ep_id, raw_text:$text, summary:$text, searchable_text:$text, ts:timestamp(),
                    importance:0.8, consolidated:false, embedding: vecf32($ep_vec)})
CREATE (s:State {id:$state_id, attribute:'residence', value:$city, active:true, valid_from:0})
CREATE (ep)-[:INVOLVES]->(e), (ep)-[:HAS_STATE]->(s), (s)-[:OF_ENTITY]->(e)
"""


@pytest.fixture
def tenants(registry, make_ns, embedder):
    alice, bob = make_ns(owner="alice"), make_ns(owner="bob")
    for ns, who, city in ((alice, "a", "Lisbon"), (bob, "b", "Nairobi")):
        text = f"I live in {city}"
        ns.query(WRITE_TENANT, {"ep_id": f"ep-{who}", "state_id": f"st-{who}", "city": city, "text": text,
                                "ent_vec": embedder.embed_one("User"), "ep_vec": embedder.embed_one(text)})
    return alice, bob


def test_same_entity_name_resolves_per_tenant(tenants):
    alice, bob = tenants
    q = "MATCH (:Entity {key:'user'})<-[:OF_ENTITY]-(s:State {active:true}) RETURN s.value"
    assert alice.ro_query(q) == [["Lisbon"]]
    assert bob.ro_query(q) == [["Nairobi"]]


def test_vector_search_does_not_cross_tenants(tenants, embedder):
    alice, bob = tenants
    q = ("CALL db.idx.vector.queryNodes('Episode', 'embedding', 50, vecf32($v)) YIELD node, score "
         "RETURN node.id, 1 - score")
    # Bob's exact text is the perfect match, yet Alice's graph cannot return it.
    rows = alice.ro_query(q, {"v": embedder.embed_one("I live in Nairobi")})
    assert [r[0] for r in rows] == ["ep-a"]


def test_fulltext_does_not_cross_tenants(tenants):
    alice, bob = tenants
    q = "CALL db.idx.fulltext.queryNodes('Episode', $q) YIELD node RETURN node.id"
    assert alice.ro_query(q, {"q": "Nairobi"}) == []
    assert bob.ro_query(q, {"q": "Nairobi"}) == [["ep-b"]]


def test_counts_and_deletes_are_scoped(tenants, registry):
    alice, bob = tenants
    count = "MATCH (n) WHERE NOT n:Attribute AND NOT n:AttrAlias RETURN count(n)"
    assert alice.ro_query(count) == bob.ro_query(count) == [[3]]
    alice.query("MATCH (n:Episode) DETACH DELETE n")
    assert bob.ro_query("MATCH (n:Episode) RETURN n.id") == [["ep-b"]]


def test_agents_cannot_open_each_others_namespace(tenants, registry):
    from domesday.db.registry import AccessDenied

    alice, bob = tenants
    with pytest.raises(AccessDenied):
        registry.open("alice", bob.kind, bob.ident)
    with pytest.raises(AccessDenied):
        registry.open("bob", alice.kind, alice.ident)
