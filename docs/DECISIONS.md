# Decision log

One line of rationale each. "E#" refers to the experiment table in `docs/PLAN.md` section 1.

| # | Date | Decision | Rationale |
|---|---|---|---|
| D1 | 10-15 | Build on `D:\Domesday`; venv and pip/HF caches on D:; Docker disk moved to `D:\DockerData`. | C: had 3.5 GB free and the Docker disk image alone is 16.7 GB. |
| D2 | 10-15 | Python 3.11, `falkordb` client 1.8, pydantic v2, official `mcp` SDK (FastMCP over SSE), pytest. | Matches the original stack and the SDK ecosystem; 3.11 is safer for wheels than 3.14. |
| D3 | 10-15 | One FalkorDB graph per namespace (`dd:{kind}:{id}`), plus a `dd:registry` graph. | Full isolation including indexes (E8), and writes to different graphs run in parallel (E14: 3.4x at 16 graphs). |
| D4 | 10-15 | Code never accepts raw graph names; `Namespace` handles come from the registry and are membership-checked. | Leakage becomes structurally impossible rather than a WHERE clause someone can forget. |
| D5 | 10-15 | Multi-namespace reads fan out per graph and fuse in the app. | FalkorDB has no cross-graph queries. |
| D6 | 10-15 | Supersession is one Cypher query (match, retire, create, link), with no app locks. | Single queries are atomic (E9) and per-graph serialised: 400 concurrent writes gave a perfectly linear chain, while read-then-write forked 379 times (E10). |
| D7 | 10-15 | States are bitemporal (`valid_from/valid_to` plus `created_at/superseded_at`). | Separates "what was true at T" from "what we believed at T"; required for point-in-time queries and backfilled facts. |
| D8 | 10-15 | Out-of-order facts (event time older than the current state) are threaded into the chain, not made current. | Otherwise storing a past fact late would wrongly overwrite the present. |
| D9 | 10-15 | Attribute drift fix: `Attribute` nodes with `AttrAlias`; resolve by exact, seeded synonyms, then entity-scoped ANN; existing keys are fed back into the extraction prompt. | Fixes "city vs location" at write time, learns aliases so later matches are exact, and scoping to the entity's own attributes limits false merges. |
| D10 | 10-15 | Attributes carry `cardinality` (`one` supersedes, `many` accumulates). | "likes sushi" then "likes pizza" must not supersede; the original conflated these. |
| D11 | 10-15 | Entities are canonical nodes keyed by normalised name; surface forms are `Alias` nodes with `ALIAS_OF`; creation uses `MERGE` on the key. | `MERGE` is race-safe under concurrency (E11) and aliases stay queryable. |
| D12 | 10-15 | The cosine threshold of 0.93 is applied as `distance <= 0.07`. | FalkorDB returns cosine distance, not similarity (E1). |
| D13 | 10-15 | All timestamps are epoch-ms integers with range indexes. | `datetime()` is unsupported; integers are index-friendly (E15). |
| D14 | 10-15 | Fulltext uses DDL `CREATE FULLTEXT INDEX`; the lane builds an OR query from content tokens and min-max normalises to [0.72, 1.0]. | The procedure form takes 1 arg in 6.0, terms are ANDed, and scores are unbounded (E6, E7). |
| D15 | 10-15 | Vector lanes over-fetch with the dynamic pool and post-filter. | No vector pre-filtering (E2). |
| D16 | 10-15 | App-side dimension validation; the registry stores `embed_model`/`embed_dim` per namespace. | Mismatched vectors are accepted silently and become unsearchable (E4). |
| D17 | 10-15 | Add Lane 5 (entity anchor: query entities plus 1-hop `RELATED` neighbours). | Makes retrieval depend on graph structure, not just the indexes, and helps alias and multi-hop questions. |
| D18 | 10-15 | Materialise `(Entity)-[:RELATED]->(Entity)` alongside `Relation` nodes. | Algorithms (PageRank, shortestPath, label propagation) need direct entity-entity edges. |
| D19 | 10-15 | PageRank is precomputed onto `Entity.pagerank` by maintenance; it is not personalised per query. | FalkorDB PageRank is global (E16); used to prioritise entities and states within the context budget. |
| D20 | 10-15 | Label-propagation communities drive consolidation batches and dedup candidates. | Semantically coherent batches give better summaries than arbitrary age buckets. |
| D21 | 10-15 | Superseded facts' episodes are not filtered out of retrieval; expansion labels states CURRENT or SUPERSEDED-by. | Historical questions need them; labelling lets the answering LLM distinguish now from then. |
| D22 | 10-15 | The async buffer writes a raw `pending` Episode to the graph immediately; a worker enriches it; failures become `status:'failed'` and can be replayed. | Fixes weakness (d): durable, crash-safe, visible to all agents rather than one process's memory. |
| D23 | 10-15 | Extraction is validated by pydantic, retried once with the error, then quarantined as `extraction_failed` (still text-searchable) and surfaced in `memory_config`. | Fixes weakness (c): errors become visible and recoverable instead of silent. |
| D24 | 10-15 | Coordination graph: Agent, Task (`DEPENDS_ON`, lease), Handoff (`CARRIES` episodes); claims are a single conditional query. | Exactly-once claims verified with 40 racing agents (E12); leases recover crashed agents. |
| D25 | 10-15 | Procedural memory: Procedure, Step, and Run nodes, versioned with `SUPERSEDES`, ranked by goal similarity × Wilson lower bound of success. | Reuses the temporal chain for versioning; Wilson keeps a 1/1 procedure from outranking 40/45. |
| D26 | 10-15 | Conversation history as Session, then Turn linked by `NEXT`, with `FOLLOWS` between sessions and `PRODUCED` to episodes. | Ordered history across sessions with provenance from memory back to the conversation. |
| D27 | 10-15 | Providers: `fake` (deterministic: hashed n-gram embeddings, rule-based extractor), `fastembed` (local bge-small, no key), OpenAI, Anthropic (LLM only), Ollama. | Tests run offline; the demo runs without keys; Anthropic has no embedding API. |
| D28 | 10-15 | Compose sets `FALKORDB_ARGS` (higher `MAX_QUEUED_QUERIES`, explicit `TIMEOUT_DEFAULT/TIMEOUT_MAX`) and a data volume; the Browser port is configurable (3001 locally because 3000 is taken). | Image defaults (`TIMEOUT 1000`, queue 25) are tight for a concurrency demo (E17). |
| D29 | 10-15 | Cypher is co-located as named constants; a script exports them to `docs/QUERIES.md`. | Readable modules plus the submission's "queries the product depends on" artifact. |
| D30 | 10-15 | Evaluation is re-run honestly under `bench/` against vector-RAG and chat-buffer baselines with the same providers; the original 61.3% is not claimed. | The brief requires measured, not repeated, numbers. |
| D31 | 10-15 | Supersession orders states by `valid_from` inside the single query and links each new state between its valid-time neighbours. | The naive "retire all active" form gave 6 simultaneous point-in-time answers once writers committed out of order (E19); the ordered form is exact under 300 shuffled concurrent writes. |
| D32 | 10-15 | Dependency checks use pattern comprehensions, not `EXISTS {}`. | `EXISTS` subqueries fail to parse on FalkorDB 6.0 (E18). |
| D33 | 10-15 | Compose `FALKORDB_ARGS`: `MAX_QUEUED_QUERIES 1000 TIMEOUT_DEFAULT 30000 TIMEOUT_MAX 120000 RESULTSET_SIZE 100000`, plus `--appendonly yes`; verified with `GRAPH.CONFIG GET`. | The image passes `FALKORDB_ARGS` verbatim to `--loadmodule`, so overriding it replaces the tight defaults; `TIMEOUT` is deprecated in favour of the default/max pair. |
| D34 | 10-15 | The client also passes a per-query timeout (`DOMESDAY_QUERY_TIMEOUT_MS`, default 30 s) on every call. | Works against any server, including one started with the image's `TIMEOUT 1000` (verified: a 70 ms query that defaults would cut off runs with an explicit timeout). |
| D35 | 10-15 | One `redis.BlockingConnectionPool` per process, handed to `FalkorDB(connection_pool=...)`. | A plain pool raises when exhausted; a blocking pool makes bursts wait instead of failing. |
| D36 | 10-15 | `Namespace` handles carry the member's role (`owner`/`writer`/`reader`): readers get `ro_query` only, only owners can drop the graph. | Lets an agent read team memory without being able to change it, enforced in the handle rather than at each call site. |
| D37 | 10-15 | Index setup diffs against `db.indexes()` and tolerates "already indexed". | FalkorDB 6.0 raises on re-creating an index, and `db.indexes()` raises on a graph that doesn't exist yet; both verified live. |
| D38 | 10-15 | Seeded `Attribute` nodes have no embedding at seed time; the write path fills it lazily. | Keeps schema setup independent of the embedding provider; the ANN layer only compares attributes an entity already holds anyway. |
| D39 | 10-15 | LLM calls take `task`/`payload` hints and always return token counts. | Real models ignore the hints; the offline fake uses them to answer deterministically. Token counts fix the original's "stores log 0 tokens" limitation. |
| D40 | 10-15 | Anthropic provider defaults to `claude-opus-5-5`, effort `medium`, structured JSON output, and server-side `fallbacks: "default"`. | Current default model; structured output removes most extraction parse failures; fallback re-runs a safety decline instead of losing the write. Override with `DOMESDAY_LLM_MODEL`. |
| D41 | 10-15 | Local embeddings default to fastembed `BAAI/bge-small-en-v1.5` (384-d) cached under `HF_HOME/fastembed`. | No key and no server; 65 MB on D:. |
