"""Live experiments against a local FalkorDB container.

Run: python experiments/exp_falkordb.py
Each section prints what it observed; findings are summarised in docs/PLAN.md.
"""
import math
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from falkordb import FalkorDB

db = FalkorDB(host="localhost", port=6379)
PREFIX = "exp_"


def fresh(name):
    g = db.select_graph(PREFIX + name)
    try:
        g.delete()
    except Exception:
        pass
    return db.select_graph(PREFIX + name)


def section(title):
    print(f"\n=== {title} ===")


def try_q(g, q, params=None, label=None):
    try:
        r = g.query(q, params or {})
        print(f"OK   {label or q[:70]!r}: {r.result_set[:5]}")
        return r
    except Exception as e:
        print(f"FAIL {label or q[:70]!r}: {type(e).__name__}: {e}")
        return None


def unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


# ---------------------------------------------------------------- vectors
section("1. vector index: score semantics, params, post-filtering")
g = fresh("vec")
try_q(g, "CREATE VECTOR INDEX FOR (e:Episode) ON (e.embedding) "
         "OPTIONS {dimension:4, similarityFunction:'cosine'}")
vecs = {
    "same": [1, 0, 0, 0],
    "close": [0.9, 0.1, 0, 0],
    "orth": [0, 1, 0, 0],
    "opposite": [-1, 0, 0, 0],
}
for name, v in vecs.items():
    g.query("CREATE (:Episode {id:$id, ns:$ns, embedding: vecf32($v)})",
            {"id": name, "ns": "a" if name != "close" else "b", "v": v})
g.query("CREATE (:Episode {id:'noembed', ns:'a'})")  # node without the property
r = g.query("CALL db.idx.vector.queryNodes('Episode','embedding',10,vecf32($q)) "
            "YIELD node, score RETURN node.id, score ORDER BY score",
            {"q": [1, 0, 0, 0]})
print("query [1,0,0,0] ->", r.result_set)
try_q(g, "RETURN vec.cosineDistance(vecf32([1,0,0,0]), vecf32([0.9,0.1,0,0]))")
try_q(g, "RETURN vec.euclideanDistance(vecf32([1,0,0,0]), vecf32([0,1,0,0]))")
try_q(g, "CALL db.idx.vector.queryNodes('Episode','embedding',10,vecf32($q)) "
         "YIELD node, score WHERE node.ns = 'a' RETURN node.id, score",
      {"q": [1, 0, 0, 0]}, "post-filter WHERE after YIELD")
try_q(g, "MATCH (e:Episode {id:'same'}) RETURN e.embedding")
try_q(g, "CREATE (:Episode {id:'baddim', embedding: vecf32([1,2])})", label="wrong dimension insert")
try_q(g, "MATCH (e:Episode {id:'orth'}) SET e.embedding = vecf32([0.99,0.01,0,0]) RETURN e.id",
      label="update vector")
r = g.query("CALL db.idx.vector.queryNodes('Episode','embedding',2,vecf32($q)) "
            "YIELD node, score RETURN node.id, score", {"q": [1, 0, 0, 0]})
print("after update, k=2 ->", r.result_set)
try_q(g, "CALL db.indexes()")

# ---------------------------------------------------------------- fulltext
section("2. fulltext index: score scale, multi-field, params, updates")
g = fresh("ft")
try_q(g, "CALL db.idx.fulltext.createNodeIndex('Episode', 'raw_text', 'summary', 'searchable_text')")
docs = [
    ("e1", "I moved from San Francisco to New York", "relocation to New York"),
    ("e2", "Lunch with Priya at the cafe", "lunch"),
    ("e3", "New York winters are cold; I live in New York now", "living in New York"),
]
for i, raw, summ in docs:
    g.query("CREATE (:Episode {id:$i, raw_text:$r, summary:$s, searchable_text:$r})",
            {"i": i, "r": raw, "s": summ})
try_q(g, "CALL db.idx.fulltext.queryNodes('Episode', $q) YIELD node, score "
         "RETURN node.id, score ORDER BY score DESC", {"q": "New York"}, "param query 'New York'")
try_q(g, "CALL db.idx.fulltext.queryNodes('Episode', $q) YIELD node, score "
         "RETURN node.id, score ORDER BY score DESC", {"q": "live|lunch"}, "OR query")
try_q(g, "CALL db.idx.fulltext.queryNodes('Episode', $q) YIELD node, score "
         "RETURN node.id, score", {"q": "%Priay%1"}, "fuzzy")
try_q(g, "CALL db.idx.fulltext.queryNodes('Episode', $q) YIELD node, score "
         "RETURN node.id, score", {"q": "where do I live?"}, "raw question punctuation")
g.query("MATCH (e:Episode {id:'e2'}) SET e.raw_text = 'Dinner with Priya in Brooklyn'")
try_q(g, "CALL db.idx.fulltext.queryNodes('Episode', 'Brooklyn') YIELD node RETURN node.id",
      label="index sees SET update")
try_q(g, "CALL db.idx.fulltext.createNodeIndex('Entity', 'name')")
g.query("CREATE (:Entity {name:'San Francisco'}), (:Entity {name:'New York City'})")
try_q(g, "CALL db.idx.fulltext.queryNodes('Entity', 'york') YIELD node RETURN node.name",
      label="entity name fulltext lowercase")

# ---------------------------------------------------------------- multi-graph
section("3. multi-graph isolation")
ga, gb = fresh("tenant_a"), fresh("tenant_b")
ga.query("CREATE (:Episode {id:'secret-a', text:'alpha'})")
gb.query("CREATE (:Episode {id:'secret-b', text:'beta'})")
print("tenant_a sees:", ga.query("MATCH (n) RETURN n.id").result_set)
print("tenant_b sees:", gb.query("MATCH (n) RETURN n.id").result_set)
ga.query("CREATE VECTOR INDEX FOR (e:Episode) ON (e.embedding) OPTIONS {dimension:8, similarityFunction:'cosine'}")
gb.query("CREATE VECTOR INDEX FOR (e:Episode) ON (e.embedding) OPTIONS {dimension:4, similarityFunction:'cosine'}")
print("per-graph index dims differ OK:",
      [r[0:3] for r in ga.query("CALL db.indexes()").result_set],
      [r[0:3] for r in gb.query("CALL db.indexes()").result_set])
print("list_graphs:", sorted(x for x in db.list_graphs() if x.startswith(PREFIX)))
try_q(ga, "MATCH (n) RETURN count(n)")
t0 = time.perf_counter()
for i in range(200):
    db.select_graph(f"{PREFIX}many_{i}").query("CREATE (:X {i:$i})", {"i": i})
print(f"created 200 graphs in {time.perf_counter()-t0:.2f}s")
for i in range(200):
    db.select_graph(f"{PREFIX}many_{i}").delete()

# ---------------------------------------------------------------- atomic writes
section("4. single-query supersession (match-set-create-link)")
g = fresh("super")
g.query("CREATE (:Entity {name:'user'})")
SUPERSEDE = """
MATCH (e:Entity {name:$entity})
OPTIONAL MATCH (e)-[:HAS_STATE]->(old:State {attribute:$attr, active:true})
WITH e, collect(old) AS olds
CREATE (e)-[:HAS_STATE]->(s:State {attribute:$attr, value:$value, active:true,
        created_at:timestamp(), writer:$w})
FOREACH (o IN olds | SET o.active = false, o.superseded_at = timestamp()
                     CREATE (s)-[:SUPERSEDES]->(o))
RETURN s.value, size(olds)
"""
for v in ["Austin", "San Francisco", "New York"]:
    try_q(g, SUPERSEDE, {"entity": "user", "attr": "city", "value": v, "w": "seq"}, f"supersede -> {v}")
try_q(g, "MATCH (:Entity {name:'user'})-[:HAS_STATE]->(s:State {active:true}) RETURN s.value",
      label="current state")
try_q(g, "MATCH p=(s:State {active:true})-[:SUPERSEDES*]->(o) RETURN [n IN nodes(p) | n.value]",
      label="history chain")

section("4b. failed query rolls back? (error mid-query)")
g = fresh("rollback")
try_q(g, "CREATE (:A {v:1}) WITH 1 AS x UNWIND [1, 0] AS d RETURN 1 / d", label="div by zero after create")
try_q(g, "CREATE (:A {v:1}) WITH 1 AS x RETURN toInteger('x') + [1]", label="type error after create")
print("nodes after failed queries:", g.query("MATCH (a:A) RETURN count(a)").result_set)

# ---------------------------------------------------------------- concurrency
section("5. concurrent supersession: single-query vs read-then-write")


def run_concurrent(fn, n_workers, n_tasks):
    with ThreadPoolExecutor(n_workers) as ex:
        return list(ex.map(fn, range(n_tasks)))


g = fresh("race_atomic")
g.query("CREATE (:Entity {name:'user'})")
t0 = time.perf_counter()
run_concurrent(lambda i: g.query(SUPERSEDE, {"entity": "user", "attr": "city",
                                              "value": f"city{i}", "w": f"w{i}"}), 32, 400)
dt = time.perf_counter() - t0
active = g.query("MATCH (s:State {active:true}) RETURN count(s)").result_set[0][0]
in_deg = g.query("MATCH (s:State) OPTIONAL MATCH (n)-[:SUPERSEDES]->(s) "
                 "WITH s, count(n) AS c RETURN max(c), sum(c)").result_set
chain = g.query("MATCH (s:State {active:true}) MATCH p=(s)-[:SUPERSEDES*0..]->(o) "
                "RETURN max(length(p))").result_set
print(f"atomic: 400 writes / 32 threads in {dt:.2f}s ({400/dt:.0f}/s); active={active}; "
      f"max/sum in-degree={in_deg}; longest chain={chain}")

g = fresh("race_naive")
g.query("CREATE (:Entity {name:'user'})")


def naive(i):
    olds = g.query("MATCH (:Entity {name:'user'})-[:HAS_STATE]->(s:State {attribute:'city', active:true}) "
                   "RETURN ID(s)").result_set
    time.sleep(random.random() * 0.002)
    g.query("MATCH (e:Entity {name:'user'}) CREATE (e)-[:HAS_STATE]->(n:State {attribute:'city', "
            "value:$v, active:true}) WITH n UNWIND $olds AS oid MATCH (o) WHERE ID(o) = oid "
            "SET o.active=false CREATE (n)-[:SUPERSEDES]->(o)",
            {"v": f"city{i}", "olds": [r[0] for r in olds]})


run_concurrent(naive, 32, 400)
active = g.query("MATCH (s:State {active:true}) RETURN count(s)").result_set[0][0]
forks = g.query("MATCH (s:State) OPTIONAL MATCH (n)-[:SUPERSEDES]->(s) WITH s, count(n) AS c "
                "WHERE c > 1 RETURN count(s)").result_set[0][0]
print(f"naive read-then-write: active={active}, states superseded by >1 writer (forks)={forks}")

section("6. concurrent MERGE on same entity name")
g = fresh("merge")
run_concurrent(lambda i: g.query("MERGE (e:Entity {name:$n}) ON CREATE SET e.by=$i",
                                 {"n": "Google", "i": i}), 32, 200)
print("Google nodes after 200 concurrent MERGEs:", g.query("MATCH (e:Entity {name:'Google'}) RETURN count(e)").result_set)

section("7. atomic task claim")
g = fresh("claim")
g.query("UNWIND range(1,20) AS i CREATE (:Task {id:i, status:'open'})")
CLAIM = """
MATCH (t:Task {status:'open'})
WITH t ORDER BY t.id LIMIT 1
SET t.status = 'claimed', t.owner = $agent
RETURN t.id
"""
claims = run_concurrent(lambda i: g.query(CLAIM, {"agent": f"agent{i}"}).result_set, 40, 40)
won = [c[0][0] for c in claims if c]
claimed = g.query("MATCH (t:Task {status:'claimed'}) RETURN count(t)").result_set
print(f"40 agents, 20 tasks: {len(won)} claims, unique={len(set(won))}, tasks claimed={claimed}")
CLAIM_ONE = """
MATCH (t:Task {id:$id}) WHERE t.status = 'open'
SET t.status = 'claimed', t.owner = $agent RETURN t.owner
"""
g.query("CREATE (:Task {id:999, status:'open'})")
res = run_concurrent(lambda i: g.query(CLAIM_ONE, {"id": 999, "agent": f"a{i}"}).result_set, 40, 40)
print("40 agents racing for task 999 -> winners:", [r for r in res if r])

section("8. client thread safety + mixed read/write throughput + queue limits")
g = fresh("tp")
g.query("UNWIND range(1,2000) AS i CREATE (:Episode {id:i, imp: rand()})")
g.query("CREATE INDEX FOR (e:Episode) ON (e.id)")
errors = []


def mixed(i):
    try:
        if i % 5 == 0:
            g.query("CREATE (:Episode {id:$i, imp:rand()})", {"i": 100000 + i})
        else:
            g.ro_query("MATCH (e:Episode) WHERE e.imp > 0.9 RETURN count(e)")
    except Exception as e:
        errors.append(str(e))


for workers in (8, 32, 64):
    errors.clear()
    t0 = time.perf_counter()
    run_concurrent(mixed, workers, 3000)
    dt = time.perf_counter() - t0
    print(f"{workers} threads: 3000 ops in {dt:.2f}s = {3000/dt:.0f} ops/s, errors={len(errors)} "
          f"{set(errors) if errors else ''}")
print("config:", db.config_get("MAX_QUEUED_QUERIES"), db.config_get("TIMEOUT"),
      db.config_get("TIMEOUT_DEFAULT"), db.config_get("THREAD_COUNT"))

# ---------------------------------------------------------------- functions
section("9. temporal + math functions, subqueries, list params")
g = fresh("fn")
for q in [
    "RETURN timestamp()",
    "RETURN datetime()",
    "RETURN localdatetime('2025-03-01T10:00:00')",
    "RETURN date('2025-03-01')",
    "RETURN exp(-log(2) * 365 / 365.0)",
    "RETURN toFloat(timestamp() - 86400000)",
    "CALL { RETURN 1 AS x } RETURN x",
    "UNWIND $rows AS r CREATE (n:R) SET n = r RETURN count(n)",
    "WITH {a:1} AS m RETURN m.a, keys(m)",
    "RETURN coalesce(null, 'x'), toLower('ABC') CONTAINS 'b'",
    "RETURN [x IN [3,1,2] WHERE x > 1 | x * 2]",
    "MATCH (n:R) WITH n ORDER BY n.k DESC RETURN collect(n.k)[0..2]",
]:
    try_q(g, q, {"rows": [{"k": 1, "v": "a"}, {"k": 2, "v": "b"}]})

section("10. algorithms")
g = fresh("algo")
g.query("""
CREATE (a:Entity {name:'Alice'}), (b:Entity {name:'Bob'}), (c:Entity {name:'Acme'}),
       (d:Entity {name:'Paris'}), (e:Entity {name:'Zed'}), (f:Entity {name:'Yan'}),
       (a)-[:REL]->(b), (b)-[:REL]->(c), (a)-[:REL]->(c), (c)-[:REL]->(d),
       (e)-[:REL]->(f)
""")
try_q(g, "CALL algo.pageRank('Entity', 'REL') YIELD node, score RETURN node.name, score ORDER BY score DESC")
try_q(g, "CALL algo.labelPropagation({nodeLabels:['Entity'], relationshipTypes:['REL']}) "
         "YIELD node, communityId RETURN communityId, collect(node.name)")
try_q(g, "CALL algo.WCC({nodeLabels:['Entity']}) YIELD node, componentId RETURN componentId, collect(node.name)")
try_q(g, "MATCH (a:Entity {name:'Alice'}), (d:Entity {name:'Paris'}) "
         "WITH shortestPath((a)-[:REL*]->(d)) AS p RETURN [n IN nodes(p) | n.name]")
try_q(g, "MATCH (a:Entity {name:'Alice'}), (d:Entity {name:'Paris'}) "
         "MATCH p = allShortestPaths((a)-[:REL*]-(d)) RETURN [n IN nodes(p) | n.name]")
try_q(g, "MATCH (a:Entity {name:'Alice'}) CALL algo.BFS(a, 3, 'REL') YIELD nodes RETURN [n IN nodes | n.name]")
try_q(g, "CALL algo.betweenness({nodeLabels:['Entity']}) YIELD node, score RETURN node.name, score")

for name in db.list_graphs():
    if name.startswith(PREFIX):
        db.select_graph(name).delete()
print("\ncleaned up")
