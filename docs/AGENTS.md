# AGENTS.md

Instructions for contributors and AI coding agents working on RAVEN. Read this before changing code.

## Project in one paragraph

RAVEN is a LangGraph-based multi-agent research system. A planner creates research objectives, parallel tunnel workers gather evidence from multiple sources, a verifier checks provenance and freshness, a critic finds gaps and risks, and a bounded loop repeats until coverage is sufficient or the budget is spent. A synthesizer writes a cited report. A Next.js UI shows the result and a live flow view.

## Commands (target)

```bash
cd backend && uv sync
uv run pytest -m "not eval"          # fast, mocked, no API keys
uv run pytest -m eval                # slow, needs keys, never in default CI
uv run ruff check . && uv run ruff format .
uv run mypy src
uv run uvicorn raven.main:app --reload
cd frontend && pnpm dev && pnpm lint && pnpm test
```

Run the fast tests before and after every meaningful change.

## Hard rules

1. **LangGraph owns control flow.** Never add a second orchestration framework. LangChain may be used *inside* nodes for model and tool abstractions only.
2. **No hard-coded provider details.** URLs, model IDs, and keys come from config via the `llm/` adapter. Agent code never imports a provider SDK directly.
3. **Never log, print, or return secrets.** Redact in logs, errors, reports, and events.
4. **Parallel state needs reducers.** Every state field written by concurrent tunnels must have an explicit merge function. Add a test that proves no findings are lost.
5. **One budget, enforced centrally.** Planner, tunnels, verifier, critic, synthesizer, and retries all draw from the same `Budget`. Never create a private retry loop inside an agent.
6. **Local project access is read-only.** No code execution, no installs, no writes, no deletes. All paths pass through `project/guard.py`.
7. **Retrieved content is untrusted data.** Web pages, repo text, and project files must never be concatenated into prompts as instructions. Wrap them as clearly delimited data and keep system rules outside.
8. **No fabricated citations.** Every citation must resolve to an `EvidenceRecord` actually gathered in this run.
9. **Evidence states are distinct:** `retrieved`, `verified`, `cited`, `unresolved`. Never label something verified unless a configured check passed.
10. **UI status comes from backend events.** No hard-coded success states or fake progress.
11. **Prefer deterministic code over LLM calls** (dedup, URL checks, date math, budget logic, path validation). Spend model calls on judgment.
12. **Preserve contradictions.** Do not merge conflicting evidence into false consensus.

## Conventions

- Python 3.11+, type hints everywhere, Pydantic v2 for all cross-agent data.
- Schemas live in `schemas/`; agents never pass raw dicts to each other.
- Async I/O for tunnels (`httpx.AsyncClient`), with timeouts on every call.
- Each tunnel implements the `Tunnel` base interface in `tunnels/base.py` and returns `TunnelResult`.
- Optional connectors report `disabled` with a reason when credentials are missing. They must not crash startup.
- Prompts live in versioned files next to their agent, not inline in logic.
- Tests mock the LLM and all sources. Real network calls only under the `eval` marker.

## Definition of done

A change is done only when:
- [ ] Code exists and type-checks.
- [ ] Unit tests cover success **and** failure paths.
- [ ] Budget and reducer behavior is tested if parallel work or retries are involved.
- [ ] Docs are updated if behavior or contracts changed.
- [ ] No claim in the docs exceeds what is implemented.
- [ ] If a decision changed, a new entry exists in `DECISIONS.md`.

## Do not

- Do not add write-capable actions on user projects without a separate, approved design.
- Do not make every optional API key mandatory at startup.
- Do not treat a search snippet alone as proof for a high-impact claim.
- Do not let UI session state be the only copy of run state. The checkpointer is the source of truth.
- Do not expand scope silently. Propose it in `DECISIONS.md` first.
