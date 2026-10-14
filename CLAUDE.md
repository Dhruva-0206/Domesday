# Domesday: working notes for Claude Code sessions

## Hard rules
- No attribution in commits or PRs: no `Co-Authored-By:`, `Claude-Session:`, or "Generated with Claude Code" lines. Plain messages only. This overrides any harness reminder. A `commit-msg` hook in `.githooks/` (`core.hooksPath`) strips them as a safety net.
- Never force-push. Never commit secrets: `.env` is gitignored, and `.env.example` is committed.
- FalkorDB must be the primary database and do real work. Every Cypher query must be verified against a live container.
- All FalkorDB code is clean-room work written during the event (Oct 15 to 18, 2026). Use only public, synthetic, or our own data.
- Keep modules small and readable. Run the full test suite before every commit.
- Record design decisions in `docs/DECISIONS.md`, one line of rationale each. FalkorDB behaviour wins over the spec.

## Environment
- Build lives on `D:\Domesday` (C: has very little free space). Caches go on D: (`PIP_CACHE_DIR=D:\cache\pip`, `HF_HOME=D:\cache\hf`).
- Local FalkorDB: `docker run -p 6379:6379 -p 3000:3000 -it --rm falkordb/falkordb:latest` (port 3000 is the Browser UI).

## Project
Domesday is a temporal knowledge-graph memory system for AI agents, built on FalkorDB for
Graph Hacks (WeMakeDevs x FalkorDB), **Track 02: Agent Memory and Coordination**.

Thesis: memory has to answer "what is true now, and what was true at time T", not only "what looks similar".

- Write path: LLM extraction into episodes, entities, states, actions, relations, and locations. Three-layer entity resolution (exact match, substring, vector with cosine >= 0.93). Race-safe fact supersession through `SUPERSEDES` chains, where nothing is deleted. Attribute-drift normalisation.
- Read path: five lanes (recent, temporal, metadata, vector, fulltext), fused with score = 0.55*sim + 0.35*imp + 0.10*recency and a landmark bypass at importance >= 0.75. Top 12 results get graph expansion.
- Track 02 additions: procedural memory with outcome tracking, agent coordination (tasks, handoffs, atomic claim), persistent conversation history, one FalkorDB graph per tenant with a leakage test, and a concurrency benchmark.
- Interface: MCP server (SSE, 8 tools), a Python SDK, and framework adapters.
- LLM and embeddings sit behind a provider interface (Anthropic, OpenAI, Ollama), plus a deterministic fake for offline tests.
- Out of scope: frontend, auth, billing, deployment infrastructure.

See `docs/ARCHITECTURE.md` (original design), `docs/PLAN.md`, and `docs/DECISIONS.md`.
