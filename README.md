# Domesday

Temporal knowledge-graph memory for AI agents, built on [FalkorDB](https://www.falkordb.com/) for
Graph Hacks (Track 02: Agent Memory and Coordination).

Memory has to answer "what is true now, and what was true at time T", not only "what looks similar".

Work in progress: see `docs/PLAN.md` for the design and `docs/DECISIONS.md` for the decision log.

## Quick start (development)

```bash
docker compose up -d                  # FalkorDB with tuned FALKORDB_ARGS (see docker-compose.yml)
python -m venv .venv && .venv/Scripts/activate   # or source .venv/bin/activate
pip install -e ".[dev,all]"
cp .env.example .env                  # defaults use the offline fake providers
pytest                                # unit tests + integration tests against live FalkorDB
```
