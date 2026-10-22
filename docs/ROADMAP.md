# ROADMAP.md

Build order from scratch. Each phase has an **exit criterion**. Do not start the next phase until it is met.

## Phase 0: Feasibility (do this first)
- Run a throwaway script against OpenCode Zen: text, structured output, tool calling, latency, rate limits.
- Decide embeddings: Zen endpoint or local model.
- Confirm Tavily works. Check GitHub token and (optionally) X access.
- Measure typical latency to validate the 120 s default.
- Record outcomes in `DECISIONS.md` (ADR-003, Q1, Q2, Q4).
**Exit:** Big Pickle capability matrix documented; fallback chosen if needed.

## Phase 1: Foundations
- Repo scaffold, `pyproject.toml`, lint, type check, pytest, CI.
- `config.py` with env settings and secret redaction.
- Schemas: `ResearchObjective`, `ResearchPlan`, `EvidenceRecord`, `TunnelResult`, `CriticReport`, `Report`, `Event`.
- LLM adapter + fake LLM for tests. `BudgetGate`.
**Exit:** Schemas and budget gate fully unit-tested with mocks.

## Phase 2: Core graph (idea mode, one tunnel)
- Graph state with reducers (`evidence`, `tunnel_status`, `usage`).
- Analyzer (idea), planner, approval interrupt, fan-out, web tunnel (Tavily), normalizer.
- SQLite checkpointer + app DB + Alembic.
- Event emission and persistence.
**Exit:** Mocked end-to-end run completes; checkpoint resume works; reducer test passes.

## Phase 3: Verify and loop
- Verifier (deterministic checks + batched relevance + freshness).
- Critic (coverage, contradictions, risks, gaps), gap planner, routing, stop reasons.
- Synthesizer + citation resolution check + one repair.
**Exit:** Loop test cases pass (gap closes, no_progress, budget_exhausted, max_rounds). Citation resolution = 100% on mocks.

## Phase 4: API and first UI
- FastAPI endpoints + SSE with replay.
- Next.js: input, plan review, live flow view, report view with citations and freshness badges.
- Typography and design tokens from `UI_DESIGN.md`.
**Exit:** A user can run an idea from the browser and watch real events drive the flow.

## Phase 5: More tunnels
- GitHub, official docs, papers. Community (X) only if credentials and plan exist.
- Per-source freshness signals, caching, rate-limit handling.
**Exit:** Multiple tunnels run concurrently; one failing tunnel does not lose others' results.

## Phase 6: Project mode
- `project/` connector: guard, inventory, indexer, citations.
- Stack and dependency detection, version vs. latest check.
- Embeddings + vector retrieval, incremental re-index.
- Project-error findings with file path and line range.
- UI: folder picker, files-consulted view.
**Exit:** Fixture-repo tests pass, including secrets exclusion, traversal, and symlink cases.

## Phase 7: Memory and history
- Project memory, episodic memory, approved decisions, user controls to view/export/delete.
**Exit:** Re-running on the same project reuses the index and cached sources, and re-verifies freshness.

## Phase 8: Evaluation and hardening
- Eval dataset, scoring scripts, prompt-injection suite.
- Tune limits and thresholds from measured data.
- Security review, dependency audit, accessibility pass.
**Exit:** All MVP criteria below are met and CI gates are green.

## MVP acceptance checklist

- [x] Submit an idea or a project folder from the UI.
- [x] Planner produces structured objectives; user can edit and approve.
- [x] Multiple tunnels run concurrently with provenance on every record.
- [x] Evidence is normalised, de-duplicated, verified, and labelled for freshness.
- [x] Critic reports coverage, contradictions, feature risks, and errors.
- [x] Targeted follow-up runs only within limits; clean stop with reason.
- [x] Report has resolvable citations and discloses unresolved gaps.
- [x] Project mode cites relative paths and line ranges; secrets excluded; path escapes blocked.
- [x] Live flow view reflects real backend state.
- [x] Big Pickle capabilities verified through the adapter.
- [x] Tests green; metrics measured and recorded.

**Sign-off Status**: All 11 MVP acceptance criteria verified and passed. CI gates green (105 pytest unit/eval tests pass, ruff clean, mypy strict clean across 67 backend source files, tsc clean across frontend). Evaluation results recorded in `evals/results/latest_results.json` with 100.0% citation resolution rate and zero secret exposure.
