# Domesday on FalkorDB: Build Plan

Track: **Graph Hacks, Track 02: Agent Memory and Coordination**.
Original design: `docs/ARCHITECTURE.md`. Decision log: `docs/DECISIONS.md`.
Evidence for every FalkorDB claim below is in `experiments/` (scripts and their `.out.txt` output),
run against `falkordb/falkordb:latest` (graph module 6.0.2) with the Python client `falkordb` 1.8.0.

---

## 1. What the experiments established

| # | Question | Finding | Consequence |
|---|---|---|---|
| E1 | What does `db.idx.vector.queryNodes` return as `score` for a cosine index? | **Cosine distance**: 0 means identical, 1 orthogonal, 2 opposite. `vec.cosineDistance()` agrees. | similarity = `1 - score`. The entity threshold "cosine >= 0.93" becomes `score <= 0.07`. |
| E2 | Can vector search be pre-filtered? | No. `WHERE` after `YIELD` post-filters the top-k. | Over-fetch using the dynamic pool, then filter. Graph-per-tenant removes the most selective filter. |
| E3 | Parameterised vectors? | `vecf32($v)` with a Python list works. Updating a vector property re-indexes it. Nodes without the property are skipped. | All vectors are passed as parameters, never string-formatted. |
| E4 | Dimension mismatch? | A 2-d vector written into a 4-d index is **accepted silently** (stored but not searchable). | The app validates dimensions. The registry records model and dimension per namespace. |
| E5 | ANN plus graph expansion in one query? | `CALL db.idx.vector.queryNodes(...) YIELD node, score OPTIONAL MATCH (node)-[:INVOLVES]->...` works. | One round trip per lane, including expansion. |
| E6 | Fulltext creation? | `db.idx.fulltext.createNodeIndex` accepts **at most 1 argument** in 6.0 (the docs' multi-field form fails). DDL `CREATE FULLTEXT INDEX FOR (e:Episode) ON (e.a, e.b, e.c)` works. | Use the DDL form. |
| E7 | Fulltext query semantics? | Terms are implicitly **AND**ed: "where do I live?" returns nothing, while `live\|lunch` matches. Stemming works (moving matches moved). Scores are raw, unbounded TF-IDF. | The lane builds an OR query from content tokens (stopwords and punctuation removed), then min-max normalises to [0.72, 1.0]. |
| E8 | Multi-graph isolation? | Graphs are fully isolated, including indexes (different vector dimensions per graph). Names such as `dd:user:alice` work. 200 graphs were created in 0.09 s. | One graph per namespace. |
| E9 | Is one Cypher query atomic? | Yes. A runtime error after `CREATE`/`SET` rolls back everything (0 nodes left, property unchanged). | Each critical mutation is one query. |
| E10 | Concurrent supersession (400 writes, 32 threads, one key) | **Single-query** form: exactly 1 active state, a perfectly linear chain (max in-degree 1, length 399), 3,675 writes/s. **Read-then-write** form: 18 active states and 379 forks. | Writes are serialised per graph, so one-query supersession is race-free with no locks. The naive version is the anti-pattern shown in the demo. |
| E11 | Concurrent `MERGE` of the same entity (200 calls, 32 threads) | 1 node. | `MERGE` on a normalised key is the dedup guard for entity creation. |
| E12 | Atomic claim | 40 agents racing for 20 tasks: 20 claims, all unique. 40 agents racing for 1 task: exactly 1 winner. `WITH t ORDER BY ... LIMIT 1` before `SET` works; LIMIT only fails to limit *eager* clauses that come before it. | Claim, lease, and handoff are single conditional queries. |
| E13 | Is the client thread-safe, and what throughput? | One `FalkorDB` client shared by 8/32/64 threads: about 6,200 to 7,300 mixed ops/s, 0 errors. | One client per process with a connection pool. |
| E14 | Write parallelism across graphs | 64 heavy writes: 1 graph 280/s, 4 graphs 742/s, 16 graphs 947/s. | Writes serialise **per graph**, so graph-per-tenant also gives write scalability. |
| E15 | Temporal functions | `timestamp()` (epoch ms), `date()`, and `localdatetime()` work. **`datetime()` does not exist.** A range index on an integer `ts` is used by `>=`/`<` filters. | All times are stored as epoch-ms integers with range indexes. |
| E16 | Algorithms | `algo.pageRank(label, rel)`, `algo.labelPropagation({...})`, `algo.WCC({...})`, `algo.BFS(node, depth, rel)`, `algo.betweenness({...})`, and the `shortestPath()` function work. `allShortestPaths` needs endpoints bound by a preceding `WITH`. PageRank is global, not personalised. | See section 6 for where each one does real work. |
| E17 | Image defaults | The image loads with `TIMEOUT 1000`, `MAX_QUEUED_QUERIES 25`, `THREAD_COUNT 16`. | `docker-compose.yml` sets explicit `FALKORDB_ARGS` (raised queue limit, `TIMEOUT_DEFAULT`/`TIMEOUT_MAX`). |
| E18 | `EXISTS { MATCH ... }` subquery | **Not supported** (parse error). Pattern comprehension `size([(t)-[:DEPENDS_ON]->(d) WHERE ... \| d]) = 0` works. | Dependency checks use pattern comprehension. |
| E19 | Simple supersession with concurrent writers whose event times arrive out of order | Commit order is not valid-time order, so `valid_to` is set inconsistently and point-in-time lookups return 6 "true" values. | Supersession **must order by valid time inside the query** (below): 300 shuffled concurrent writes gave 1 active state, a sorted 299-edge chain, 0 gaps, and exact point-in-time answers. |

---

## 2. Tenancy model

- **One FalkorDB graph per namespace**, named `dd:{kind}:{id}` where kind is `user | team | project | sandbox`.
- **Registry graph** `dd:registry`:
  `(:Namespace {name, kind, created_at, embed_model, embed_dim, schema_version})`,
  `(:Agent {id, name})-[:MEMBER_OF {role}]->(:Namespace)`.
- Application code never takes a raw graph name. Every operation receives a `Namespace` handle resolved
  through the registry, and an agent may only open namespaces it is a `MEMBER_OF`.
- Reading "my memory + my team's memory" fans out one query per graph (cross-graph Cypher does not exist) and fuses the results in the app.
- Isolation test: two tenants hold identical entity names and contradictory facts. Every public operation is checked for leakage.

## 3. Graph data model (per namespace graph)

### Semantic and episodic memory
```
(:Episode {id, raw_text, summary, searchable_text, emotion, importance, ts, event_ts,
           speaker, session_id, embedding, embedding_model, status, consolidated})
(:Entity  {key, name, kind, embedding, pagerank, community, created_at})
(:Alias   {key, name})                                  -[:ALIAS_OF]->     (:Entity)
(:Attribute {key, description, cardinality, embedding}) <-[:ALIAS_OF]-     (:AttrAlias {key})
(:State   {id, attribute, value, active, valid_from, valid_to, created_at, superseded_at, confidence})
(:Action  {id, description, ts})
(:Role    {name, description})
(:Relation{id, description, relation_type})
(:Location{key, name, lat, lon, place_type})

(Episode)-[:INVOLVES]->(Entity)
(Episode)-[:HAS_STATE]->(State)-[:OF_ENTITY]->(Entity)
(State)-[:OF_ATTRIBUTE]->(Attribute)
(State)-[:SUPERSEDES]->(State)                      temporal chain, newest -> older
(Episode)-[:HAS_ACTION]->(Action)-[:BY_ENTITY]->(Entity), (Action)-[:ON_ENTITY]->(Entity)
(Episode)-[:HAS_ROLE]->(Role)-[:OF_ENTITY]->(Entity)
(Episode)-[:HAS_RELATION]->(Relation)-[:FROM_ENTITY]->(Entity), (Relation)-[:TO_ENTITY]->(Entity)
(Entity)-[:RELATED {type, weight}]->(Entity)        materialised, used by graph algorithms
(Episode)-[:AT_LOCATION]->(Location)
(Episode)-[:CONSOLIDATED_INTO]->(Episode)           consolidation provenance
```
States are **bitemporal**. `valid_from`/`valid_to` record when the fact was true in the world.
`created_at`/`superseded_at` record when we learned it and when we stopped believing it.

### Conversation history
```
(:Session {id, agent_id, started_at, ended_at, summary})-[:FIRST_TURN]->(:Turn)
(:Turn {id, seq, role, content, ts})-[:NEXT]->(:Turn)
(Session)-[:FOLLOWS]->(Session)        previous session of the same agent in the same namespace
(Turn)-[:PRODUCED]->(Episode)          turns that were committed to memory
```

### Procedural memory
```
(:Procedure {id, name, goal, goal_embedding, version, active, successes, failures, last_used})
(Procedure)-[:HAS_STEP {order}]->(:Step {instruction, tool})
(Procedure)-[:SUPERSEDES]->(Procedure)              a refined procedure replaces the old version
(:Run {id, outcome, notes, ts, duration_ms})-[:OF_PROCEDURE]->(Procedure)
(Agent)-[:EXECUTED]->(Run), (Procedure)-[:USES]->(Entity)
```
Retrieval by goal ranks on goal similarity × the Wilson lower bound of the success rate.

### Coordination (in team or project graphs)
```
(:Agent {id, name, role, last_seen})
(:Task {id, title, description, status, priority, created_at, claimed_by, lease_until, result})
(Task)-[:DEPENDS_ON]->(Task)
(Agent)-[:CREATED]->(Task), (Agent)-[:CLAIMED {at}]->(Task)
(:Handoff {id, note, ts, status})-[:FROM_AGENT]->(Agent), (Handoff)-[:TO_AGENT]->(Agent),
(Handoff)-[:FOR_TASK]->(Task), (Handoff)-[:CARRIES]->(Episode)
```

### Indexes (per graph)
- Vector (cosine): `Episode.embedding`, `Entity.embedding`, `Attribute.embedding`, `Procedure.goal_embedding`
- Fulltext: `Episode(raw_text, summary, searchable_text)`, `Procedure(name, goal)`
- Range: `Episode.id`, `Episode.ts`, `Episode.event_ts`, `Entity.key`, `Alias.key`, `Attribute.key`, `AttrAlias.key`,
  `State.id`, `Task.id`, `Task.status`, `Session.id`, `Procedure.id`

## 4. Key Cypher (verified forms; final versions live next to the code)

**Race-free, valid-time-ordered supersession**, a single query (E9, E10, E19; verified in `experiments/exp_plan_queries2.py`).
The new state is inserted between its valid-time neighbours and becomes active only if nothing newer exists:
```cypher
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
```
Multi-valued attributes (`cardinality:'many'`) skip this query and only create the state.

**Point in time**: `MATCH (e:Entity {key:$k})<-[:OF_ENTITY]-(s:State {attribute:$a}) WHERE s.valid_from <= $t AND (s.valid_to IS NULL OR s.valid_to > $t) RETURN s`

**Atomic claim**, honouring dependencies and expired leases (E12, E18):
```cypher
MATCH (t:Task) WHERE t.status = 'open' OR (t.status = 'claimed' AND t.lease_until < $now)
WITH t WHERE size([(t)-[:DEPENDS_ON]->(d:Task) WHERE d.status <> 'done' | d]) = 0
WITH t ORDER BY t.priority DESC, t.created_at LIMIT 1
SET t.status = 'claimed', t.claimed_by = $agent, t.lease_until = $now + $lease_ms
RETURN t.id
```

**Vector lane with post-filter**: `CALL db.idx.vector.queryNodes('Episode','embedding',$k,vecf32($q)) YIELD node, score WHERE node.consolidated = false RETURN node.id, 1 - score AS sim`

## 5. Write path
1. **Extraction**: the LLM returns structured JSON validated by pydantic. On failure it retries once with the validation error. If it fails again, the episode is stored as `status:'extraction_failed'`: still searchable by text, flagged in `memory_config`, and replayable.
   The prompt includes the **existing canonical attribute keys** of the entities mentioned, so the model reuses vocabulary.
2. **Entity resolution** (a read, done before writing): (1) exact normalised `key` on Entity or Alias; (2) substring containment, up to 5 candidates, longest wins; (3) ANN on `Entity.embedding` with distance <= 0.07. A match creates an `Alias`, otherwise the entity is `MERGE`d by key (E11 makes creation race-safe).
3. **Attribute resolution** (the drift fix): (1) exact key or AttrAlias; (2) seeded synonym groups (for example `city/location/lives_in/home → residence`); (3) ANN on `Attribute.embedding`, restricted to attributes the entity already holds; (4) otherwise a new Attribute. Every non-exact match records an `AttrAlias` so it becomes exact next time. Attributes have a `cardinality` (`one` supersedes, `many` accumulates).
4. **Write**: the episode, its edges, and each supersession are written in single atomic queries.
5. **Async mode**: `store()` writes the raw Episode immediately with `status:'pending'`, so it is durable and visible to *every* agent through the fulltext and recent lanes. A worker then enriches it. Pending episodes are re-queued on startup, and after 3 failures they become `status:'failed'` (listed by `memory_config`, replayable).

## 6. Read path
Lanes run in parallel per namespace:
- L0 recent (5 min, 0.25)
- L1 temporal (parsed ranges on `event_ts`/`ts`, 1.15)
- L2 metadata (1.10)
- L3 vector (dynamic k = max(50, min(500, 0.02·N)), sim = 1 - d)
- L4 fulltext (OR query, normalised to [0.72, 1.0])
- **L5 entity-anchor (new)**: entities named in the query are resolved through layers 1 and 2, and their episodes plus those of 1-hop `RELATED` neighbours are scored 0.95 × edge weight

Fusion: `sim = max(lane scores)`, then `score = 0.55·sim + 0.35·importance + 0.10·recency`, with recency `exp(-ln2·age_days/365)` and recency forced to 1.0 when importance >= 0.75. Merge by id, keep the top 12, then expand each with entities, states (marked CURRENT or SUPERSEDED-by), actions, location, and SUPERSEDES context.

Where the **graph algorithms** do real work:
- **PageRank** over `Entity-RELATED` is recomputed by maintenance and stored on `Entity.pagerank`. It orders which entities and states make it into the limited context budget.
- **`shortestPath`** between two entities named in a query adds the connecting path to the context (multi-hop questions).
- **Label propagation** communities drive consolidation (low-importance episodes are summarised per entity community, not in arbitrary batches) and produce dedup candidates.
- **BFS over `DEPENDS_ON`** computes which tasks a failed or blocked task blocks, for handoff notes.

## 7. Module layout
```
src/domesday/
  config.py              env settings
  db/       client.py (pool, Namespace), registry.py, schema.py (indexes, seeding)
  providers/ base.py, fake.py, anthropic.py, openai.py, ollama.py, fastembed.py
  extract/  models.py (pydantic), prompts.py, extractor.py
  write/    entities.py, attributes.py, supersede.py, episodes.py, buffer.py
  read/     timeparse.py, lanes.py, scoring.py, expand.py, retrieve.py, answer.py
  procedural.py  coordination.py  conversation.py
  maintenance/ consolidate.py, dedup.py, backup.py, reindex.py, centrality.py
  memory.py   facade (Memory)
  sdk/      middleware.py, agent.py, adapters/
  mcp_server.py
tests/      unit (fake providers) + integration (live FalkorDB)
bench/      concurrency benchmark, synthetic eval dataset and harness
```
Cypher lives as named constants next to the code that uses it. `scripts/dump_queries.py` collects them into `docs/QUERIES.md` for the submission.

## 8. Phases (deadline: Oct 18, 23:59 IST)
| Phase | Deliverable | Target |
|---|---|---|
| 1 Foundation and tenancy | package, config, compose, client, registry, schema and indexes, providers (fake, fastembed, OpenAI, Anthropic, Ollama), isolation test | Oct 15 |
| 2 Write path | extraction, entity and attribute resolution, bitemporal supersession, concurrency test | Oct 15-16 |
| 3 Read path | six lanes, scoring, expansion, answer | Oct 16 |
| 4 Interface | MCP server (SSE), SDK levels 1-3, adapters, **end-to-end demo pushed** | Oct 16 |
| 5 Track 02 | procedural memory, conversation history, coordination and handoffs, concurrency benchmark | Oct 17 |
| 6 Hardening | async buffer, consolidation, dedup, backup and clear, reindex, honest eval under `bench/` | Oct 17-18 |
| 7 Submission | README (setup, data model, queries, AI disclosure, Cloud notes), video script, blog draft | Oct 18 |

## 9. Changes forced by FalkorDB (versus the Neo4j original)
1. The vector score is a **distance**, so all similarity thresholds are converted (E1).
2. There is no vector pre-filtering, so we post-filter and over-fetch, and use tenancy by graph instead of a `speaker` filter (E2, E8).
3. Fulltext needs the DDL form, query terms are ANDed, and scores are unbounded. We use an OR query builder and per-query normalisation (E6, E7).
4. `datetime()` doesn't exist, so times are epoch-ms integers with range indexes (E15).
5. Vector dimension mismatches are silent, so the app validates them and the registry tracks model and dimension (E4).
6. Per-graph write serialisation replaces Neo4j transactions and locks: one query is one isolated unit (E9, E10, E14).
7. Cross-graph queries are impossible, so multi-namespace retrieval fans out in the app.
8. There is no APOC, so backup is paginated Cypher export to JSON. `allShortestPaths` needs `WITH`-bound endpoints (E16).
9. The image's default `TIMEOUT 1000` / `MAX_QUEUED_QUERIES 25` would fail under load, so they are overridden in compose (E17).
10. `EXISTS { }` subqueries are unsupported, so we use pattern comprehensions (E18).
