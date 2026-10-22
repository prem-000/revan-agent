# RAVEN: Project Intelligence & Research Agent

> **Plan. Research in parallel. Verify. Re-check. Explain the flow.**

RAVEN is a multi-agent research system for students and developers. Give it a **project idea** or point it at an **existing project folder**. It plans the research, sends specialised agents to check multiple sources at the same time, verifies the evidence (including how recent it is), finds gaps and risks, loops back within strict limits, and shows the result as a cited report with a live visual flow of how the research happened.

Built from scratch. Not a fork of any existing agent repository.

---

## What it does

| Step | What happens |
|---|---|
| 1. Intake | You type an idea **or** select a local project folder and ask a question. |
| 2. Analyze | In project mode, RAVEN reads permitted files (read-only) to detect the stack, dependencies, and entry points. |
| 3. Plan | A planner turns the input into research objectives with acceptance criteria. You review and approve. |
| 4. Research | Source agents run **in parallel**: GitHub, official docs, research papers, web, developer community, and the local project. |
| 5. Verify | Evidence is normalised, de-duplicated, and checked for provenance, relevance, and freshness. |
| 6. Critique | A critic compares evidence to objectives and flags gaps, contradictions, feature risks, and errors. |
| 7. Loop | Unresolved gaps trigger **targeted** follow-up research, bounded by a shared budget. |
| 8. Report | A synthesizer writes a cited report. Unresolved questions are disclosed, never hidden. |
| 9. Show | The UI renders the report with strong typography and a live flow diagram driven by real backend events. |

## Two input modes

- **Existing project:** "Review my project and research how to add a knowledge graph without replacing my database." Recommendations cite external sources **and** project-relative file paths with line ranges.
- **New idea:** "I want to build a train-booking assistant that checks alternate routes." Output is a report, option comparison, architecture proposal, risks, and roadmap.

## Core principles

1. **Evidence before claims.** No fabricated citations. Unsupported claims are marked unverified or omitted.
2. **Freshness is a feature.** Every source carries `published_at`, `updated_at`, and `checked_at`, and stale sources are flagged.
3. **Bounded loops.** The graph owns the loop. Agents cannot trigger unlimited research.
4. **Read-only local access.** RAVEN never runs, installs, edits, or deletes anything in your project.
5. **Untrusted content.** Web pages, repos, and project files are data, never instructions.
6. **Observable.** Every UI status comes from a real backend event.

## Tech stack

| Layer | Choice |
|---|---|
| Orchestration | LangGraph (parallel fan-out, loops, checkpoints) |
| Language | Python 3.11+ |
| LLM | OpenCode Zen, `big-pickle`, behind a replaceable adapter |
| Backend API | FastAPI + Server-Sent Events |
| Frontend | Next.js (React), Tailwind, React Flow for the flow view |
| Validation | Pydantic v2 |
| Database | SQLite (local) → PostgreSQL (multi-user) |
| Vectors | sqlite-vec / brute-force (local) → pgvector (prod) |
| Testing | pytest, mocked providers, eval harness |

Full rationale: [DECISIONS.md](DECISIONS.md). Memory and database details: [MEMORY_AND_DATA.md](MEMORY_AND_DATA.md).

## Repository layout (target)

```text
raven/
├── README.md  AGENTS.md  ARCHITECTURE.md  AGENT_ROLES.md
├── RESOURCE_POLICY.md  EVALUATION.md  DECISIONS.md
├── MEMORY_AND_DATA.md  SECURITY.md  UI_DESIGN.md  ROADMAP.md
├── .env.example
├── backend/
│   ├── pyproject.toml
│   ├── src/raven/
│   │   ├── main.py              # FastAPI app, SSE endpoint
│   │   ├── config.py            # settings from env
│   │   ├── llm/                 # provider adapter (Zen / Big Pickle)
│   │   ├── schemas/             # plan, evidence, critic, report, events
│   │   ├── graph/               # state, builder, routing, budget
│   │   ├── agents/              # analyzer, planner, verifier, critic, synthesizer
│   │   ├── tunnels/             # base, github, docs, papers, web, community
│   │   ├── project/             # connector, inventory, indexer, citations, guard
│   │   ├── memory/              # checkpoints, store, cache, vectors
│   │   ├── events/              # event bus + SSE encoding
│   │   └── db/                  # models + Alembic migrations
│   └── tests/
├── frontend/                    # Next.js app
└── evals/                       # datasets + scoring scripts
```

## Getting started (once scaffolding exists)

Prerequisites: Python 3.11+, [uv](https://docs.astral.sh/uv/), Node 20+, pnpm.

```bash
# 1. Configure
cp .env.example .env            # add ZEN_API_KEY and TAVILY_API_KEY

# 2. Backend
cd backend
uv sync
uv run pytest -m "not eval"     # mocked tests, no keys needed
uv run uvicorn raven.main:app --reload

# 3. Frontend
cd ../frontend
pnpm install
pnpm dev                        # http://localhost:3000
```

**Before building anything else**, run the Big Pickle capability check (text, structured output, tool calling). See [ROADMAP.md](ROADMAP.md), Phase 0.

## Documentation map

| File | Purpose |
|---|---|
| [AGENTS.md](AGENTS.md) | Rules for contributors and AI coding agents working on this repo |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Graph, state, routing, events, persistence |
| [AGENT_ROLES.md](AGENT_ROLES.md) | Every agent's job, inputs, outputs, limits |
| [RESOURCE_POLICY.md](RESOURCE_POLICY.md) | Budgets, rate limits, caching, stop reasons |
| [EVALUATION.md](EVALUATION.md) | Metrics, test layers, eval cases, CI gates |
| [DECISIONS.md](DECISIONS.md) | Architecture decision records |
| [MEMORY_AND_DATA.md](MEMORY_AND_DATA.md) | Memory types, database, schema, retention |
| [SECURITY.md](SECURITY.md) | Threat model and safeguards |
| [UI_DESIGN.md](UI_DESIGN.md) | Screens, flow view, typography, design tokens |
| [ROADMAP.md](ROADMAP.md) | Phased build plan with exit criteria |

## Status

**Specification stage.** Nothing here claims a feature is implemented. Mark a capability done only after code exists **and** tests pass.
