"""Fix the two failures from exp_plan_queries.py:
1. claim without EXISTS {} subqueries;
2. valid-time-ordered supersession that stays correct when writers commit out of order."""
import random
from concurrent.futures import ThreadPoolExecutor

from falkordb import FalkorDB

db = FalkorDB(host="localhost", port=6379)


def fresh(name):
    g = db.select_graph(name)
    try:
        g.delete()
    except Exception:
        pass
    return db.select_graph(name)


g = fresh("exp_claim2")
g.query("""
CREATE (a:Task {id:'a', status:'open', priority:1, created_at:1}),
       (b:Task {id:'b', status:'open', priority:9, created_at:2}),
       (c:Task {id:'c', status:'done', priority:1, created_at:0}),
       (d:Task {id:'d', status:'open', priority:5, created_at:3}),
       (s:Task {id:'stale', status:'claimed', priority:3, created_at:4, lease_until:10, claimed_by:'dead'}),
       (b)-[:DEPENDS_ON]->(a), (d)-[:DEPENDS_ON]->(c)
""")
CLAIM = """
MATCH (t:Task) WHERE t.status = 'open' OR (t.status = 'claimed' AND t.lease_until < $now)
WITH t WHERE size([(t)-[:DEPENDS_ON]->(d:Task) WHERE d.status <> 'done' | d]) = 0
WITH t ORDER BY t.priority DESC, t.created_at LIMIT 1
SET t.status = 'claimed', t.claimed_by = $agent, t.lease_until = $now + $lease_ms
RETURN t.id
"""
with ThreadPoolExecutor(10) as ex:
    res = list(ex.map(lambda i: g.query(CLAIM, {"now": 100, "agent": f"ag{i}", "lease_ms": 1000}).result_set,
                      range(10)))
print("claim order (expect d, stale, a; b blocked):", [r[0][0] for r in res if r])
print(g.query("MATCH (t:Task) RETURN t.id, t.status, t.claimed_by ORDER BY t.id").result_set)

# Valid-time ordered insert. The new state is placed between its valid-time neighbours:
#   prev = latest state with valid_from <= new.valid_from
#   next = earliest state with valid_from >  new.valid_from
# Only when there is no `next` does the new state become active. The chain stays sorted.
SUPERSEDE = """
MATCH (e:Entity {key:$entity})
OPTIONAL MATCH (e)<-[:OF_ENTITY]-(x:State {attribute:$attr})
WITH e, x ORDER BY x.valid_from
WITH e, [s IN collect(x) WHERE s.valid_from <= $valid_from] AS before,
        [s IN collect(x) WHERE s.valid_from >  $valid_from] AS after
WITH e, CASE WHEN size(before) = 0 THEN null ELSE before[-1] END AS prev,
        CASE WHEN size(after) = 0 THEN null ELSE after[0] END AS nxt
CREATE (s:State {id:$id, attribute:$attr, value:$value, valid_from:$valid_from,
                 created_at:timestamp(), active: nxt IS NULL,
                 valid_to: CASE WHEN nxt IS NULL THEN null ELSE nxt.valid_from END})-[:OF_ENTITY]->(e)
FOREACH (p IN CASE WHEN prev IS NULL THEN [] ELSE [prev] END |
    SET p.active = false, p.valid_to = $valid_from,
        p.superseded_at = coalesce(p.superseded_at, timestamp())
    CREATE (s)-[:SUPERSEDES]->(p))
FOREACH (n IN CASE WHEN nxt IS NULL THEN [] ELSE [nxt] END | CREATE (n)-[:SUPERSEDES]->(s))
WITH s, prev, nxt
OPTIONAL MATCH (nxt)-[old:SUPERSEDES]->(prev)
DELETE old
RETURN s.active
"""
g = fresh("exp_super2")
g.query("CREATE (:Entity {key:'user'})")
times = list(range(1000, 1300))
random.seed(7)
random.shuffle(times)
with ThreadPoolExecutor(32) as ex:
    list(ex.map(lambda t: g.query(SUPERSEDE, {"entity": "user", "attr": "residence", "id": f"s{t}",
                                               "value": f"city{t}", "valid_from": t}), times))
active = g.query("MATCH (s:State {active:true}) RETURN s.value").result_set
deg = g.query("MATCH (s:State) OPTIONAL MATCH (s)-[r:SUPERSEDES]->() WITH s, count(r) AS c RETURN max(c)").result_set
indeg = g.query("MATCH (s:State) OPTIONAL MATCH ()-[r:SUPERSEDES]->(s) WITH s, count(r) AS c RETURN max(c)").result_set
chain = g.query("MATCH (s:State {active:true}) MATCH p=(s)-[:SUPERSEDES*0..]->(o) "
                "RETURN max(length(p))").result_set
sorted_ok = g.query("MATCH (a:State)-[:SUPERSEDES]->(b:State) WHERE a.valid_from <= b.valid_from "
                    "RETURN count(*)").result_set
gaps = g.query("MATCH (a:State)-[:SUPERSEDES]->(b:State) WHERE b.valid_to <> a.valid_from "
               "RETURN count(*)").result_set
print(f"shuffled 300 concurrent: active={active}, max out={deg}, max in={indeg}, chain={chain}, "
      f"misordered edges={sorted_ok}, valid_to gaps={gaps}")
PIT = ("MATCH (e:Entity {key:$k})<-[:OF_ENTITY]-(s:State {attribute:$a}) "
       "WHERE s.valid_from <= $t AND (s.valid_to IS NULL OR s.valid_to > $t) RETURN s.value")
print("point-in-time 1150 ->", g.query(PIT, {"k": "user", "a": "residence", "t": 1150}).result_set,
      " 999 ->", g.query(PIT, {"k": "user", "a": "residence", "t": 999}).result_set,
      " 5000 ->", g.query(PIT, {"k": "user", "a": "residence", "t": 5000}).result_set)
for n in ("exp_claim2", "exp_super2"):
    db.select_graph(n).delete()
