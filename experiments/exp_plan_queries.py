"""Verify the exact Cypher printed in docs/PLAN.md section 4."""
from concurrent.futures import ThreadPoolExecutor

from falkordb import FalkorDB

db = FalkorDB(host="localhost", port=6379)
g = db.select_graph("exp_plan")
try:
    g.delete()
except Exception:
    pass
g = db.select_graph("exp_plan")

SUPERSEDE = """
MATCH (e:Entity {key:$entity})
OPTIONAL MATCH (e)<-[:OF_ENTITY]-(old:State {attribute:$attr, active:true})
WITH e, collect(old) AS olds
CREATE (s:State {id:$id, attribute:$attr, value:$value, active:true,
                 valid_from:$valid_from, created_at:timestamp()})-[:OF_ENTITY]->(e)
FOREACH (o IN olds | SET o.active=false, o.superseded_at=timestamp(), o.valid_to=$valid_from
                     CREATE (s)-[:SUPERSEDES]->(o))
"""
g.query("CREATE (:Entity {key:'user'})")
with ThreadPoolExecutor(32) as ex:
    list(ex.map(lambda i: g.query(SUPERSEDE, {"entity": "user", "attr": "residence", "id": f"s{i}",
                                               "value": f"city{i}", "valid_from": 1000 + i}), range(300)))
print("supersede: active =", g.query("MATCH (s:State {active:true}) RETURN count(s)").result_set,
      "max in-degree =", g.query("MATCH (s:State) OPTIONAL MATCH (n)-[r:SUPERSEDES]->(s) "
                                 "WITH s, count(r) AS c RETURN max(c)").result_set)

POINT_IN_TIME = ("MATCH (e:Entity {key:$k})<-[:OF_ENTITY]-(s:State {attribute:$a}) "
                 "WHERE s.valid_from <= $t AND (s.valid_to IS NULL OR s.valid_to > $t) RETURN s.value")
print("point-in-time t=1150 ->", g.query(POINT_IN_TIME, {"k": "user", "a": "residence", "t": 1150}).result_set)

CLAIM = """
MATCH (t:Task) WHERE (t.status = 'open' OR (t.status = 'claimed' AND t.lease_until < $now))
  AND NOT EXISTS { MATCH (t)-[:DEPENDS_ON]->(d:Task) WHERE d.status <> 'done' }
WITH t ORDER BY t.priority DESC, t.created_at LIMIT 1
SET t.status='claimed', t.claimed_by=$agent, t.lease_until=$now + $lease_ms
RETURN t.id
"""
g.query("""
CREATE (a:Task {id:'a', status:'open', priority:1, created_at:1}),
       (b:Task {id:'b', status:'open', priority:9, created_at:2}),
       (c:Task {id:'c', status:'done', priority:1, created_at:0}),
       (d:Task {id:'d', status:'open', priority:5, created_at:3}),
       (stale:Task {id:'stale', status:'claimed', priority:3, created_at:4, lease_until:10, claimed_by:'dead'}),
       (b)-[:DEPENDS_ON]->(a), (d)-[:DEPENDS_ON]->(c)
""")
with ThreadPoolExecutor(10) as ex:
    res = list(ex.map(lambda i: g.query(CLAIM, {"now": 100, "agent": f"ag{i}", "lease_ms": 1000}).result_set,
                      range(10)))
print("claims (b blocked by open a; d unblocked; stale lease reclaimable):", [r[0][0] for r in res if r])
print("owners:", g.query("MATCH (t:Task) RETURN t.id, t.status, t.claimed_by ORDER BY t.id").result_set)
g.delete()
