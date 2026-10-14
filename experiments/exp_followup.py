"""Follow-up experiments: fulltext DDL, dimension mismatch, rollback, graph names, timeouts."""
import time

from falkordb import FalkorDB

db = FalkorDB(host="localhost", port=6379)


def fresh(name):
    g = db.select_graph(name)
    try:
        g.delete()
    except Exception:
        pass
    return db.select_graph(name)


def try_q(g, q, params=None, label=None):
    try:
        r = g.query(q, params or {})
        print(f"OK   {label or q[:80]!r}: {r.result_set[:6]}")
        return r
    except Exception as e:
        print(f"FAIL {label or q[:80]!r}: {type(e).__name__}: {e}")


print("=== fulltext DDL forms ===")
g = fresh("exp_ft2")
try_q(g, "CALL db.idx.fulltext.createNodeIndex({label:'Episode', stopwords:['the']}, 'raw_text', 'summary')",
      label="procedure with options map + 2 fields")
g = fresh("exp_ft2")
try_q(g, "CREATE FULLTEXT INDEX FOR (e:Episode) ON (e.raw_text, e.summary, e.searchable_text)",
      label="DDL multi-field")
for i, raw, s in [("e1", "I moved from San Francisco to New York", "relocation"),
                  ("e2", "Lunch with Priya at the cafe", "lunch with a friend"),
                  ("e3", "New York winters are cold. I live in New York now", "living in NY")]:
    g.query("CREATE (:Episode {id:$i, raw_text:$r, summary:$s, searchable_text:$r})", {"i": i, "r": raw, "s": s})
for q in ["New York", "york", "live|lunch", "%Priay%1", "friend", "moving", "where do I live?",
          "who's Priya", "a-b", "live lunch"]:
    try_q(g, "CALL db.idx.fulltext.queryNodes('Episode', $q) YIELD node, score "
             "RETURN node.id, score ORDER BY score DESC", {"q": q}, f"ft {q!r}")
g.query("MATCH (e:Episode {id:'e2'}) SET e.raw_text = 'Dinner in Brooklyn'")
try_q(g, "CALL db.idx.fulltext.queryNodes('Episode', 'Brooklyn') YIELD node RETURN node.id", label="sees SET")
try_q(g, "CALL db.indexes() YIELD label, properties, types RETURN label, properties, types")

print("\n=== vector: dimension mismatch, k > n, ANN + expansion in one query ===")
g = fresh("exp_vec2")
g.query("CREATE VECTOR INDEX FOR (e:Episode) ON (e.embedding) OPTIONS {dimension:4, similarityFunction:'cosine'}")
try_q(g, "CREATE (:Episode {id:'bad', embedding: vecf32([1.0, 2.0])}) RETURN 1", label="2-dim into 4-dim index")
try_q(g, "MATCH (e:Episode {id:'bad'}) RETURN e.id, e.embedding", label="bad node stored?")
g.query("CREATE (a:Episode {id:'good', embedding: vecf32([1,0,0,0])})-[:INVOLVES]->(:Entity {name:'Ada'})")
try_q(g, "CALL db.idx.vector.queryNodes('Episode','embedding',50,vecf32([1,0,0,0])) YIELD node, score "
         "RETURN node.id, score", label="k=50 with tiny index")
try_q(g, "CALL db.idx.vector.queryNodes('Episode','embedding',5,vecf32($q)) YIELD node, score "
         "OPTIONAL MATCH (node)-[:INVOLVES]->(en:Entity) RETURN node.id, 1 - score AS sim, collect(en.name)",
      {"q": [1, 0, 0, 0]}, "ANN + expansion")
try_q(g, "CREATE VECTOR INDEX FOR (n:Entity) ON (n.embedding) OPTIONS {dimension:4, similarityFunction:'cosine'}",
      label="second vector index on other label")
try_q(g, "CREATE (:Episode {id:'nullvec', embedding: null}) RETURN 1", label="null embedding")

print("\n=== rollback on runtime error ===")
g = fresh("exp_rb")
g.query("CREATE (:Seed)")
try_q(g, "CREATE (:A) WITH 1 AS x UNWIND [1, 0] AS d RETURN 1 / d", label="create then div0")
try_q(g, "MATCH (s:Seed) SET s.touched = true WITH s UNWIND [1,0] AS d RETURN 1/d", label="set then div0")
print("A nodes:", g.query("MATCH (a:A) RETURN count(a)").result_set,
      "seed touched:", g.query("MATCH (s:Seed) RETURN s.touched").result_set)

print("\n=== graph names ===")
for name in ["dd:user:alice", "dd/team/x", "dd__registry", "dd:team:acme-eng.v2"]:
    try:
        g = fresh(name)
        g.query("CREATE (:X)")
        print("OK  ", name, db.select_graph(name).query("MATCH (n) RETURN count(n)").result_set)
        g.delete()
    except Exception as e:
        print("FAIL", name, e)

print("\n=== allShortestPaths with bound ends ===")
g = fresh("exp_asp")
g.query("CREATE (a:E {n:'a'})-[:R]->(b:E {n:'b'})-[:R]->(c:E {n:'c'}), (a)-[:R]->(d:E {n:'d'})-[:R]->(c)")
try_q(g, "MATCH (a:E {n:'a'}), (c:E {n:'c'}) WITH a, c MATCH p = allShortestPaths((a)-[:R*]->(c)) "
         "RETURN [x IN nodes(p) | x.n]")
try_q(g, "MATCH p = allShortestPaths((a:E {n:'a'})-[:R*]->(c:E {n:'c'})) RETURN [x IN nodes(p) | x.n]")

print("\n=== read timeout (TIMEOUT=1000 in image args) ===")
g = fresh("exp_to")
t0 = time.perf_counter()
try_q(g, "UNWIND range(1, 3000000) AS i WITH i WHERE i % 7 = 0 RETURN count(i)", label="slow read via query()")
print(f"  took {time.perf_counter()-t0:.2f}s")
t0 = time.perf_counter()
try:
    print("  ro_query:", g.ro_query("UNWIND range(1, 3000000) AS i WITH i WHERE i % 7 = 0 RETURN count(i)").result_set)
except Exception as e:
    print("  ro_query FAIL:", e)
print(f"  took {time.perf_counter()-t0:.2f}s")
try:
    print("  ro_query timeout=20000:", g.ro_query("UNWIND range(1, 3000000) AS i WITH i WHERE i % 7 = 0 "
                                                  "RETURN count(i)", timeout=20000).result_set)
except Exception as e:
    print("  FAIL:", e)

print("\n=== range index on epoch-ms timestamp ===")
g = fresh("exp_ts")
g.query("CREATE INDEX FOR (e:Episode) ON (e.ts)")
g.query("UNWIND range(1, 5000) AS i CREATE (:Episode {id:i, ts: 1700000000000 + i * 3600000})")
plan = g.explain("MATCH (e:Episode) WHERE e.ts >= $a AND e.ts < $b RETURN e.id ORDER BY e.ts DESC LIMIT 5")
print("plan:", str(plan).replace("\n", " | "))
try_q(g, "MATCH (e:Episode) WHERE e.ts >= $a AND e.ts < $b RETURN count(e)",
      {"a": 1700000000000 + 100 * 3600000, "b": 1700000000000 + 200 * 3600000})

for n in db.list_graphs():
    if n.startswith("exp_"):
        db.select_graph(n).delete()
