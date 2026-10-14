# Temporal Knowledge Graph Memory System for AI Agents
## Architecture, Design, and Technical Reference

---

## Core Concept

Most AI memory systems treat memory as a retrieval problem -- embed text, index it, find the nearest neighbours when a query arrives. This works for static knowledge bases. It fails systematically when knowledge evolves over time.

The fundamental insight driving this architecture: **retrieval systems answer "what looks like this query?" -- a memory system must answer "what is currently true, and what was true at any past moment?"**

These are different questions with different answers. Over any time horizon longer than days, conflating them produces wrong results. This system is built around that distinction.

---

## The Problem with Existing Approaches

### Vector Stores (Pinecone, Chroma, Weaviate)

- Index text chunks as flat embeddings
- Retrieve by cosine similarity to query vector
- No concept of contradiction -- "I live in San Francisco" and "I moved to New York" are both equally valid, both indexed, both retrieved
- No temporal awareness -- old facts and new facts compete on equal footing
- No entity resolution -- "Google", "my company", "work" are three separate unconnected facts

### RAG Pipelines

- Retrieve similar text, stuff into LLM context
- Stateless by design -- no memory across sessions
- Context window limits cap total knowledge
- Cannot answer "what changed since March?" or "what was true before this decision?"

### Chat History Buffers

- Simple append-only log of conversation
- Grows unbounded, no structure, no compression
- No entity tracking, no state evolution
- Useless across session boundaries

### In-House Orchestrator Memory

- Usually flat vector stores or relational tables with embeddings
- Solves persistence but not temporal evolution
- No contradiction detection
- Not benchmarked against anything

---

## Architecture Overview

The system has four major layers:

```
+-----------------------------------------------------+
|                  Interface Layer                    |
|   MCP Server (SSE)  |  REST API  |  Python SDK     |
+-----------------------------------------------------+
|                 Processing Layer                    |
|  Semantic Extraction | Entity Resolution | Scoring  |
+-----------------------------------------------------+
|                  Storage Layer                      |
|         Neo4j Temporal Knowledge Graph              |
+-----------------------------------------------------+
|                Retrieval Layer                      |
|    Five-Lane Pipeline | Score Fusion | Expansion    |
+-----------------------------------------------------+
```

---

## Layer 1 - Interface Layer

### MCP Server (Primary Interface)

The primary interface is a Model Context Protocol (MCP) server using Server-Sent Events (SSE) transport. MCP is Anthropic's open standard for connecting AI assistants to external tools.

**Transport:** SSE (persistent connection, bidirectional messaging)
**Framework:** FastMCP built on Starlette ASGI
**Server:** Uvicorn
**Authentication:** Three-method middleware (JWT, API key, OAuth token)

The MCP server exposes 8 tools to any compatible AI client:

| Tool | Purpose |
|---|---|
| store_memory | Parse and write a fact into the knowledge graph |
| query_memory | Natural language question, returns structured answer |
| retrieve_memory_context | Fetch context for a given entity or topic |
| memory_config | Inspect provider health and system status |
| backup_memory | Export full graph as portable JSON |
| deduplicate_memory_entities | Merge duplicate entity nodes |
| consolidate_memory | Summarise old low-importance episodes |
| clear_memory | Hard delete all memory in a namespace |

### REST API (Dashboard and Billing)

Authenticated REST endpoints for the dashboard console and billing:

```
POST /auth/verify          -- Token verification, returns user identity
GET  /dashboard/stats      -- Episode count, entity count, token totals
GET  /dashboard/graph      -- Graph nodes and edges for visualization
GET  /dashboard/logs       -- Request logs with token counts
GET  /dashboard/usage      -- Daily token breakdown
POST /billing/create-subscription  -- Create payment subscription
POST /billing/webhook      -- Payment provider webhook (signature verified)
POST /billing/cancel       -- Cancel subscription at period end
GET  /billing/status       -- Current plan and subscription info
POST /assistant/chat       -- AI assistant proxy
```

### Python SDK

Three abstraction levels:

**Level 1 - Raw functions:**
```python
store_memory("fact", speaker="namespace-id")
query_memory("question?", speaker="namespace-id")
```

**Level 2 - Memory middleware:**
```python
memory = ReeveMemory(
    namespace="team_memory",
    inject_mode="system",       # system | prepend | custom
    importance_threshold=0.7,
    deduplicate=True,
)
```

**Level 3 - Agent wrapper:**
```python
agent = ReeveAgent(llm=your_llm)
agent.chat("message")  # memory handled automatically
```

**Framework adapters:** LangChain, LangGraph, CrewAI, Agno, OpenAI Agents SDK

---

## Layer 2 - Processing Layer

### Semantic Extraction

Every input goes through a structured LLM extraction step. The extraction model returns a typed schema:

```
SemanticExtraction:
  summary: str                    -- concise summary of the input
  emotion: str                    -- detected emotional tone
  importance: float               -- 0.0 to 1.0 calibrated score
  entities: List[Entity]          -- named entities mentioned
  roles: List[Role]               -- relational roles
  actions: List[Action]           -- events and activities
  relations: List[Relation]       -- entity relationships
  states: List[State]             -- attribute-value pairs
  location: Optional[Location]    -- geographic context
  event_time: Optional[datetime]  -- when the event occurred
```

**Importance calibration:**
- 0.1 - 0.2: incidental filler, passing mentions
- 0.35 - 0.45: sensory detail, secondary characters
- 0.5 - 0.6: notable interactions, meaningful context
- 0.7 - 0.85: major changes, conflicts, milestones
- 0.9 - 1.0: life-defining events (birth, death, marriage, diagnosis, major career change)

### Entity Resolution (Three-Layer)

Before any entity is written to the graph, a three-layer resolution pass prevents duplicate nodes:

```
New entity mention
        |
        v
Layer 1: Case-insensitive exact match
        |-- Match found --> create ALIAS_OF edge --> done
        |-- No match
        v
Layer 2: Substring containment (up to 5 candidates, longest wins)
        |-- Match found --> create ALIAS_OF edge --> done
        |-- No match
        v
Layer 3: Vector index ANN (cosine similarity >= 0.93)
        |-- Match found --> create ALIAS_OF edge --> done
        |-- No match
        v
Create new canonical Entity node
```

This ensures "Google", "my company", "work", "the company I work at" all resolve to the same canonical node.

**Geographic resolution:**
- Place names resolved to real coordinates via geocoding
- Cross-examination against LLM knowledge to prevent misresolution
- "Ziro" correctly maps to Arunachal Pradesh not Burkina Faso
- "Nara" maps to Japan not Washington DC
- Hard arithmetic distance filtering based on real coordinates

**Image resolution:**
- Photos embedded into the same vector space as text
- Image-to-image and text-to-image retrieval
- GPS EXIF metadata auto reverse-geocoded
- Unseen images matched to previously stored similar images

### Fact Supersession

The core innovation. When a new State conflicts with an existing one for the same (entity_name, attribute) pair:

```
Step 1: Query all active State nodes for this entity + attribute
        SET active = false, superseded_at = now()

Step 2: CREATE new State node with active = true

Step 3: CREATE SUPERSEDES edge from new State to each old State
```

Result:
```
city = New York    [active=true, created=2026-06]
        |
        +-- :SUPERSEDES --> city = San Francisco [active=false, superseded=2026-06]
                                    |
                                    +-- :SUPERSEDES --> city = Austin [active=false]
```

Current-state queries return New York. Historical queries ("where before New York?") traverse SUPERSEDES chains. Nothing is deleted. Full audit trail always queryable.

### Async Write-Ahead Buffer

For low-latency ingestion, an optional async write-ahead buffer:

1. `store()` writes raw text to in-memory buffer instantly (< 1ms)
2. Returns `tmp_[id]` pending identifier to caller
3. Background worker thread dequeues entries
4. Runs full pipeline (LLM extraction + Neo4j write) asynchronously
5. Unpersisted entries immediately visible in retrieval as `[PENDING]` context lines
6. On permanent failure (3 retries): logs to `tmp_failed.json` for manual replay

```python
# In-memory buffer (thread-safe)
_entries: dict[str, PendingEntry]

# Background worker
class PersistWorker(threading.Thread):
    def run(self):
        while not stopped:
            entry = queue.get(timeout=1.0)
            self._process(entry)  # LLM extraction + Neo4j write
```

---

## Layer 3 - Storage Layer

### Neo4j Temporal Knowledge Graph

The core data store is a Neo4j property graph. Neo4j is chosen specifically because:
- Native graph traversal for relationship queries
- Vector index support (ANN search over embeddings)
- Fulltext index support (Lucene-style keyword search)
- Temporal properties on all nodes (timestamps, active flags)
- ACID transactions

### Node Types (8)

| Node | Key Properties |
|---|---|
| Episode | id, summary, raw_text, searchable_text, emotion, importance, timestamp, speaker, embedding, embedding_model, active, consolidated |
| Entity | name, embedding, canonical (boolean) |
| State | entity_name, attribute, value, active, created_at, superseded_at |
| Action | description, timestamp |
| Role | name, description |
| Location | name, latitude, longitude, place_type |
| Relation | description, relation_type |
| RequestLog | uid, operation, tokens_in, tokens_out, total_tokens, timestamp |

### Relationship Types (13)

INVOLVES, ALIAS_OF, HAS_ACTION, BY_ENTITY, ON_ENTITY, HAS_ROLE, HAS_RELATION, FROM_ENTITY, TO_ENTITY, HAS_STATE, OF_ENTITY, **SUPERSEDES**, AT_LOCATION

SUPERSEDES is the most important relationship -- it forms the temporal chain connecting evolved facts.

### Indexes

| Index | Type | Purpose |
|---|---|---|
| episode_embedding | Vector (cosine) | ANN similarity search over Episode embeddings |
| entity_embedding | Vector (cosine) | Entity resolution Layer 3 |
| episode_raw_text | Fulltext (Lucene) | Keyword search across raw_text, summary, searchable_text |

### Namespace Isolation

Every Episode node carries a `speaker` property that maps to the authenticated user's unique identifier. All queries are hard-filtered by `WHERE ep.speaker = $uid`. No cross-namespace data leakage is possible at the query level.

Multiple isolation patterns:
- Per-user: `speaker = user_id`
- Per-team: `speaker = team_id` (shared memory)
- Per-project: `speaker = "project_{project_id}"`
- Sandbox: `speaker = "playground_{user_id}"`

### Lifetime Engineering

As the graph grows, several background processes maintain quality and performance:

**Dynamic candidate pool:**
- Candidate count = max(50, min(500, total_episodes * 0.02))
- Prevents expensive full-graph scans
- Scales automatically with graph size

**Consolidation engine:**
- Targets episodes with importance <= 0.30 and age > 90 days
- LLM summarises batches into consolidated episodes
- Reduces noise from low-importance historical content

**Background deduplication:**
- Three-pass entity dedup running periodically
- Merges fragmented entity nodes that accumulated over time
- Supports dry_run preview before committing

**Model versioning:**
- Embedding model version tag on every Episode node
- Batch reindex utility for migrating to new embedding models
- No data loss when switching providers

---

## Layer 4 - Retrieval Layer

### Five-Lane Parallel Retrieval

All five lanes run simultaneously on every query. Results are merged and scored:

**Lane 0 - Recent Safety Net (score: 0.25)**
- Episodes from the last 5 minutes retrieved unconditionally
- Injected at base score 0.25 (low, just ensures recent context)
- Maximum 5 episodes
- Purpose: ensures very recent context surfaces even before vector index catches up

**Lane 1 - Temporal Matching (score: 1.15 -- highest priority)**
- Natural language time expressions parsed to ISO date ranges
- "Last 3 months", "in 2024", "March 2025", "before the move"
- Matched episodes boosted at 1.15
- Purpose: direct temporal queries bypass vector similarity entirely

**Lane 2 - Metadata Filtering (score: 1.10)**
- Age references, developmental phase labels, event markers
- Heuristic extraction from query text
- Purpose: "when I was a student", "during the project" type queries

**Lane 3 - Vector ANN Search**
- Query embedded using active embedding model
- Neo4j vector index queried with dynamic candidate count
- Each candidate scored via 3D scoring formula
- Purpose: semantic similarity retrieval

**Lane 4 - Full-Text Search (scores normalised to [0.72, 1.0])**
- Lucene-style query against episode_raw_text fulltext index
- Normalised to prevent fulltext scores dominating
- Purpose: keyword-based retrieval for exact terms

### Three-Dimensional Scoring Formula

```
score = 0.55 * vector_similarity
      + 0.35 * importance
      + 0.10 * recency

recency = exp(-ln(2) * age_days / 365.0)
# Half-life: 365 days -- a memory from 1 year ago has recency = 0.5

if importance >= 0.75:
    recency = 1.0  # Landmark memory bypass
    # High-importance events bypass recency decay entirely
    # A promotion from 10 years ago outranks yesterday's lunch note
```

**Weight rationale:**
- Similarity (0.55): highest weight because relevance to query is paramount
- Importance (0.35): deliberately higher than recency -- significance matters more than age
- Recency (0.10): lowest weight -- prevents recency bias from burying important old facts

**Landmark bypass:**
Any memory with importance >= 0.75 gets recency = 1.0 permanently. Life-defining events (promotions, relocations, marriages, diagnoses) never decay out of retrieval regardless of age.

### Score Fusion and Expansion

```
All lane results
        |
        v
_merge_ranked_candidates()
-- Deduplication by episode ID
-- Score normalisation across lanes
-- Combined ranking
        |
        v
Top 12 episodes selected
        |
        v
expand_episodes()
-- Each episode expanded with full graph context
-- Linked entities, states, actions, locations retrieved
-- SUPERSEDES chain context included
        |
        v
Formatted context string passed to LLM
        |
        v
LLM generates answer grounded in retrieved context
```

---

## Authentication Architecture

Three authentication methods tried in order:

**Method 1 - Internal JWT (HS256)**
- Signed with OAUTH_JWT_SECRET
- 1-hour TTL enforced
- `sub` claim contains user ID
- Used for dashboard and API sessions

**Method 2 - Permanent API Key**
- Format: `reeve_live_[random]`
- Stored in Neo4j User node
- Looked up via `get_user_by_api_key(token)`
- Used for SDK and MCP integrations

**Method 3 - Google OAuth Token**
- Accepts Google access tokens (opaque) and ID tokens (JWT)
- Access tokens verified via Google UserInfo API
- ID tokens verified via `google.oauth2.id_token.verify_oauth2_token()`
- `sub` claim extracted as user identifier
- Fallback: if ID token verification fails, try UserInfo API

**CORS:**
```
allow_origins: [
    "https://[primary-domain]",
    "https://www.[primary-domain]",
    "http://localhost:3000"
]
allow_credentials: true
allow_methods: ["GET", "POST", "OPTIONS"]
allow_headers: ["Authorization", "Content-Type"]
```

**Middleware execution order (Starlette reverse-registration):**
1. CORSMiddleware -- handles OPTIONS preflight first
2. ProxyHeadersMiddleware -- fixes IP headers behind reverse proxy
3. AuthMiddleware -- validates Bearer token on protected routes

---

## Tech Stack

### Backend

| Component | Choice | Reason |
|---|---|---|
| Language | Python 3.11+ | LLM SDK ecosystem, async support |
| MCP Framework | FastMCP | Native MCP protocol, SSE transport |
| Web Framework | Starlette | Lightweight ASGI, middleware composability |
| ASGI Server | Uvicorn | High-performance, production-grade |
| Graph Database | Neo4j AuraDB | Native graph traversal, vector indexes, fulltext |
| LLM (primary) | AWS Bedrock / Mistral Large 2 | Managed, scalable, no GPU needed |
| LLM (alternative) | OpenAI GPT-4o | Broad availability |
| LLM (local) | Ollama / llama3.2 | Zero cost, privacy |
| Embeddings | nomic-embed-text / text-embedding-ada-002 / Titan v2 | Provider-matched |
| Auth | PyJWT + google-auth | JWT + Google OAuth |
| Billing | Razorpay | Subscription management, webhooks |
| User Storage | Supabase (PostgreSQL) | Managed Postgres, RLS support |
| Containerization | Docker | Reproducible deployment |
| Reverse Proxy | Caddy | Automatic HTTPS, clean config |
| Cloud | AWS EC2 | Standard Linux VM |

### Frontend

| Component | Choice | Reason |
|---|---|---|
| Framework | React 19 | Ecosystem, component model |
| Build | Create React App + craco | Customizable webpack without ejecting |
| Routing | React Router 7 | SPA routing |
| Styling | TailwindCSS | Utility-first, no CSS files |
| Components | shadcn/ui (Radix UI) | Accessible, unstyled primitives |
| Icons | Lucide React | Consistent icon set |
| Auth | @react-oauth/google | Google OAuth2 implicit flow |
| Database Client | @supabase/supabase-js | Supabase JS client |
| Hosting | Vercel | Edge deployment, automatic CI/CD |

### Design System

| Token | Value |
|---|---|
| Background | #1A1916 (near-black warm) |
| Surface | rgba(255,255,255,0.04) |
| Surface 2 | rgba(255,255,255,0.07) |
| Border | rgba(225,244,246,0.1) |
| Border Strong | rgba(225,244,246,0.2) |
| Text | #E1F4F6 |
| Muted | rgba(225,244,246,0.55) |
| Accent | #56E1E9 (teal) |
| Primary | #0D6E7A (dark teal) |

Typography: serif display for headings, monospace for labels and eyebrows, system sans for body.

---

## Data Flow - End to End

### Store Flow

```
User input: "I moved from San Francisco to New York"
        |
        v
AuthMiddleware validates Bearer token
Sets current_user_id context variable (uid)
        |
        v
[If ASYNC_WRITE_ENABLED]
Write to in-memory buffer instantly
Return pending_id to user (< 1ms)
Background worker picks up asynchronously
        |
        v [or synchronously if async disabled]
operator_extract(text)
LLM extracts: entities=[User, San Francisco, New York]
              states=[city=New York (new), city=San Francisco (old)]
              importance=0.65
              emotion=neutral
        |
        v
reconcile(semantics, speaker=uid, raw_text=text)
Entity resolution: "User" -> canonical User node
                   "San Francisco" -> existing Entity node
                   "New York" -> new Entity node
        |
        v
Supersession detection:
Existing State: city=San Francisco [active=true]
New State: city=New York
Match found on (User, city)
        |
        v
Neo4j writes:
1. SET city=San Francisco [active=false, superseded_at=now()]
2. CREATE city=New York [active=true, created_at=now()]
3. CREATE (New York)-[:SUPERSEDES]->(San Francisco)
4. CREATE Episode node with embedding
5. CREATE all relationship edges
        |
        v
Return episode_id to caller
log_request(uid, "store_memory", tokens_in, tokens_out)
```

### Query Flow

```
Query: "Where do I live?"
        |
        v
AuthMiddleware validates token, sets uid
        |
        v
retrieve(query, speaker=uid)
        |
        v
Run all 5 lanes in parallel:
Lane 0: Recent episodes (last 5 min)
Lane 1: No temporal expression found -- skip
Lane 2: No metadata filter match -- skip
Lane 3: Embed query, ANN search -> [New York episode, ...]
Lane 4: Fulltext "live" -> [New York episode, San Francisco episode, ...]
        |
        v
_merge_ranked_candidates()
Score New York episode: 0.55*0.89 + 0.35*0.65 + 0.10*0.95 = 0.819
Score San Francisco episode: active=false, filtered out by WHERE active=true
        |
        v
expand_episodes([New York episode])
Fetch linked entities, states, locations
        |
        v
Context: "User moved to New York (city=New York, active=true, 2026-06)"
        |
        v
LLM generates: "You live in New York."
        |
        v
Return answer
log_request(uid, "query_memory", tokens_in, tokens_out)
```

---

## Benchmark Results

Evaluated on a 500-entry synthetic long-form narrative benchmark spanning multiple decades. 106 questions across five categories:
- Static fact lookup
- Dynamic state tracking
- Causal chain queries
- Entity alias resolution
- Multi-element recall

| System | Strict Accuracy | Lenient Accuracy | Incorrect Rate |
|---|---|---|---|
| This system | 61.3% | 84.9% | 15.1% |
| Mem0 | 40.6% | 69.8% | 30.2% |
| Vanilla Vector RAG | 39.6% | 57.5% | 42.5% |
| Supermemory | 34.9% | 65.1% | 34.9% |

**20.7 percentage point advantage** over the next-best system on strict accuracy.

Key findings:
- RAG has the highest incorrect rate (42.5%) -- confabulates when retrieved context is insufficient
- Supermemory has highest partial rate (30.2%) -- verbosity introduces drift, arithmetic errors
- The advantage is most pronounced on temporal and causal queries -- exactly where structural representation matters

---

## Known Limitations

1. **LLM extraction dependency** -- incorrect importance scores or entity misidentification propagates into the graph and is difficult to detect after the fact

2. **Structural supersession detection** -- supersession matches on (entity_name, attribute) string pair. If the LLM extracts "city" in one episode and "location" in another for the same entity, no supersession link is created and both states remain active simultaneously

3. **Synthetic benchmark** -- evaluation uses a synthetic narrative. Performance on real-world agent memory accumulated over genuine long-horizon user interactions may differ

4. **Store token tracking** -- token counts for store_memory log as 0 because the extraction pipeline does not expose token counts through the current abstraction layer. Query token tracking works correctly

5. **In-memory rate limiter** -- rate limits reset on server restart and are not shared across horizontal scale-out

6. **Page reload drops API key** -- the API key is held in memory only (not localStorage) for security. Page reload requires re-authentication

7. **No real-time sync** -- the async write buffer means very recently stored memories may not be in Neo4j yet. The [PENDING] injection in retrieval partially mitigates this but does not fully replace persistent storage

---

## Security Architecture

### Data Isolation
- Speaker/uid enforced at Neo4j query level on every operation
- No query exists that returns cross-namespace data
- RLS enabled on user storage with zero anon access
- All user upserts happen server-side using service role key

### API Key Security
- Permanent API keys never stored in browser localStorage
- Held in module-level memory variable only
- Cleared on page reload (requires re-auth)
- Rotatable via dashboard

### Transport Security
- All endpoints HTTPS only
- CORS restricted to known origins
- OPTIONS preflight handled before auth middleware
- Webhook signatures verified via HMAC-SHA256

### Input Security
- All Neo4j queries use parameterized statements (no string formatting)
- Input size limits on store operations (resource exhaustion prevention)
- Rate limiting per plan tier
- SSE connections counted toward rate limits

---

## Deployment Architecture

```
Internet
    |
    v
Caddy (reverse proxy, automatic HTTPS, Let's Encrypt)
    |
    v
Docker container (non-root user)
    |
    +--> FastMCP/Starlette server (port 8000 internal only)
    |         |
    |         +--> Neo4j AuraDB (cloud, TLS bolt+s://)
    |         +--> LLM provider (Bedrock/OpenAI/Ollama)
    |         +--> Supabase (service role, HTTPS)
    |         +--> Razorpay (HTTPS, webhook signature verified)
    |
    v
AWS EC2 (t3.micro or similar)
```

Frontend deployed separately on Vercel edge network with automatic CI/CD from main branch.

---

## Rate Limiting

| Plan | Requests / 60s | Behavior on Limit |
|---|---|---|
| Free | 220 | 429 Too Many Requests |
| Pro | 1,000 | 429 Too Many Requests |
| Enterprise | 5,000 | 429 Too Many Requests |

In-memory per-uid counter. Resets on server restart (known limitation). SSE connections count toward the limit.

---

## Billing Architecture

Subscription-based via payment provider:

1. User initiates upgrade via frontend
2. Frontend calls `POST /billing/create-subscription`
3. Backend creates subscription server-side, returns checkout URL
4. User completes payment on provider's hosted page (PCI compliant)
5. Provider calls `POST /billing/webhook` (HMAC-SHA256 verified)
6. Backend updates user plan in graph database and user storage
7. User re-authenticates to see updated plan

Subscription total_count set to 120 months (10 years) to stay within provider's end_time validation ceiling while avoiding annual re-subscription friction.

Plan enforcement happens server-side. Client-side plan gating is UI only and cannot be bypassed to access backend resources.