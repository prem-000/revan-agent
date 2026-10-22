# MEMORY_AND_DATA.md

What RAVEN remembers, where it lives, and which database to use.

## 1. Recommendation (short version)

| Need | Use now (local-first MVP) | Use later (multi-user / hosted) |
|---|---|---|
| Run state + resume | **SQLite** via LangGraph SQLite checkpointer | PostgreSQL checkpointer |
| App data (runs, plans, evidence, events, reports) | **SQLite** (WAL mode) + SQLAlchemy 2.0 + Alembic | PostgreSQL |
| Keyword search over evidence/files | SQLite **FTS5** | PostgreSQL full-text |
| Semantic search | **sqlite-vec**, or brute-force cosine in NumPy | **pgvector** |
| Fetch cache | SQLite table with TTL | Same, or Redis if many workers |
| Long-term memory | SQLite tables / LangGraph Store | PostgreSQL |
| Embeddings | Local model (e.g. a small BGE or MiniLM model via `fastembed` / `sentence-transformers`) unless Zen offers embeddings | Same or hosted embedding API |
| Queue / pub-sub | None (in-process asyncio) | Redis, only if multiple workers |

**One database file for the MVP keeps setup trivial.** Do not add Chroma, Qdrant, Neo4j, or Redis until a measured need appears (see ADR-006, ADR-007).

## 2. Memory types

| Type | What it holds | Lifetime | Storage |
|---|---|---|---|
| **Working memory** | Current graph state: plan, evidence, round, budget | One run (checkpointed) | LangGraph checkpointer |
| **Episodic memory** | Past runs: query, plan, stop reason, report, usage | Until user deletes | `runs`, `reports` tables |
| **Evidence memory** | Every gathered record with provenance and dates | Retention policy below | `evidence` table + vectors |
| **Source cache** | Fetched content by URL + hash | TTL per source type | `cache_entries` |
| **Project memory** | Project profile (stack, deps, entry points), file index, embeddings | Until files change or user clears | `projects`, `project_files`, vectors |
| **Decision memory** | Recommendations the **user approved** | Persistent | `decisions` table |
| **Preferences** | Depth profile, enabled tunnels, UI settings | Persistent | `settings` table |

### Memory rules

1. **Approval before memory.** An agent's recommendation is not stored as a project decision until the user approves it.
2. **Never store secrets.** Excluded files (`.env`, keys, certs) are never read, indexed, or embedded.
3. **Cached is not fresh.** Reports always show `checked_at`, and cached evidence is labelled with its cache age.
4. **Re-index incrementally.** Only files whose hash or mtime changed are re-embedded.
5. **User control.** The user can view, export, and delete project memory and run history.
6. **Memory is data, not instructions.** Recalled content goes into prompts as delimited data, like any other untrusted content.
7. **Use past runs carefully.** Prior evidence can seed a new run but is re-verified for freshness before it is cited.

## 3. Schema (conceptual DDL)

```sql
-- Runs and plans
CREATE TABLE runs (
  id TEXT PRIMARY KEY,
  mode TEXT NOT NULL,                 -- idea | project
  query TEXT NOT NULL,
  project_id TEXT REFERENCES projects(id),
  profile TEXT NOT NULL,              -- quick | balanced | deep
  status TEXT NOT NULL,               -- planning | awaiting_approval | running | done | stopped | failed
  stop_reason TEXT,
  llm_calls INTEGER DEFAULT 0,
  retries INTEGER DEFAULT 0,
  rounds INTEGER DEFAULT 0,
  created_at TIMESTAMP NOT NULL,
  finished_at TIMESTAMP
);

CREATE TABLE objectives (
  id TEXT PRIMARY KEY,                -- stable objective_id
  run_id TEXT NOT NULL REFERENCES runs(id),
  question TEXT NOT NULL,
  rationale TEXT,
  priority INTEGER,
  tunnels TEXT,                       -- JSON list
  expected_evidence TEXT,
  acceptance_criteria TEXT,
  coverage TEXT                       -- covered | weak | missing (latest)
);

-- Evidence ledger
CREATE TABLE evidence (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  objective_id TEXT NOT NULL REFERENCES objectives(id),
  claim TEXT NOT NULL,
  source_type TEXT NOT NULL,          -- github | docs | paper | web | community | local
  source_title TEXT,
  source_url TEXT,
  excerpt TEXT NOT NULL,
  published_at TIMESTAMP,
  updated_at TIMESTAMP,
  checked_at TIMESTAMP NOT NULL,
  freshness TEXT,                     -- fresh | aging | stale | unknown
  verification_status TEXT NOT NULL,  -- retrieved | verified | failed
  verification_method TEXT,
  confidence REAL,
  project_file TEXT,                  -- relative path for local evidence
  line_start INTEGER,
  line_end INTEGER,
  dedupe_key TEXT NOT NULL,
  extra TEXT                          -- JSON: repo, release tag, DOI, post id...
);
CREATE INDEX idx_evidence_run ON evidence(run_id);
CREATE UNIQUE INDEX idx_evidence_dedupe ON evidence(run_id, dedupe_key);

-- Critic output
CREATE TABLE findings (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES runs(id),
  round INTEGER NOT NULL,
  kind TEXT NOT NULL,                 -- gap | contradiction | feature_risk | project_error
  description TEXT NOT NULL,
  evidence_ids TEXT,                  -- JSON list
  severity TEXT
);

-- Events (powers SSE replay and the flow view)
CREATE TABLE events (
  run_id TEXT NOT NULL REFERENCES runs(id),
  seq INTEGER NOT NULL,
  ts TIMESTAMP NOT NULL,
  type TEXT NOT NULL,
  node TEXT,
  round INTEGER,
  data TEXT,                          -- JSON, redacted
  PRIMARY KEY (run_id, seq)
);

-- Reports and artifacts
CREATE TABLE reports (
  run_id TEXT PRIMARY KEY REFERENCES runs(id),
  markdown TEXT NOT NULL,
  json TEXT NOT NULL,
  citation_check TEXT                 -- result of finalize validation
);

-- Source cache
CREATE TABLE cache_entries (
  key TEXT PRIMARY KEY,               -- hash of URL/query
  source_type TEXT NOT NULL,
  content TEXT NOT NULL,
  etag TEXT,
  content_hash TEXT,
  fetched_at TIMESTAMP NOT NULL,
  expires_at TIMESTAMP NOT NULL
);

-- Local project memory
CREATE TABLE projects (
  id TEXT PRIMARY KEY,
  root_path TEXT NOT NULL,            -- approved root
  name TEXT,
  profile TEXT,                       -- JSON: languages, frameworks, deps, entry points
  last_scanned_at TIMESTAMP
);

CREATE TABLE project_files (
  project_id TEXT NOT NULL REFERENCES projects(id),
  rel_path TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  mtime TIMESTAMP,
  size_bytes INTEGER,
  language TEXT,
  PRIMARY KEY (project_id, rel_path)
);

-- Chunks + vectors (use sqlite-vec / pgvector for the embedding column or a side table)
CREATE TABLE chunks (
  id TEXT PRIMARY KEY,
  owner_type TEXT NOT NULL,           -- project_file | evidence
  owner_id TEXT NOT NULL,
  rel_path TEXT,
  line_start INTEGER,
  line_end INTEGER,
  text TEXT NOT NULL,
  embedding BLOB                      -- or vector(N) in pgvector
);

-- User-approved decisions
CREATE TABLE decisions (
  id TEXT PRIMARY KEY,
  project_id TEXT REFERENCES projects(id),
  run_id TEXT REFERENCES runs(id),
  title TEXT NOT NULL,
  rationale TEXT,
  evidence_ids TEXT,
  approved_at TIMESTAMP NOT NULL
);
```

## 4. Retention

| Data | Default retention |
|---|---|
| Checkpoints | Deleted after run completes + 7 days (keep until finished if resumable) |
| Events | Same as the run |
| Runs, reports, evidence | Until user deletes |
| Cache | TTL only |
| Project index | Until files change, project removed, or user clears |
| Logs | Short, and always redacted |

## 5. SQLite settings

- `PRAGMA journal_mode=WAL;` so reads (SSE replay) do not block writes.
- `PRAGMA foreign_keys=ON;`
- Short transactions; one writer. For heavy parallel writes, batch evidence inserts per node.
- Back up by copying the DB file while idle, or with the SQLite backup API.

## 6. Upgrade path to PostgreSQL

Trigger the move when you need: multiple users, hosted deployment, concurrent writers, or larger vector search.

1. Keep all queries behind SQLAlchemy.
2. Swap the checkpointer to the PostgreSQL one.
3. Replace sqlite-vec with `pgvector` and FTS5 with PostgreSQL full-text.
4. Run Alembic migrations, then migrate data.
5. Add Redis only if you run multiple workers and need shared cache or pub-sub for events.

## 7. What not to do

- Do not use the vector store as the source of truth. Evidence rows are.
- Do not embed excluded files "just in case".
- Do not keep full fetched pages forever; respect TTLs.
- Do not let memory recall silently override fresh evidence. Re-verify.
