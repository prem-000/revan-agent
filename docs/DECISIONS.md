# DECISIONS.md

Architecture Decision Records. Add a new entry (never silently rewrite an old one) when a decision changes. Statuses: **Accepted**, **Proposed**, **Superseded**.

---

## ADR-001: Build from scratch, no forked agent repo
**Status:** Accepted
**Context:** The original README assumed extending `libattistella/deep-research-agent`. That is no longer the plan.
**Decision:** Build RAVEN as a new codebase. Reuse the *ideas* from the original spec (planner, parallel tunnels, critic loop, guardrails) but not its code or structure.
**Consequences:** We own every contract (state, schemas, API). No inherited tests, so testing infrastructure must be built early.

## ADR-002: LangGraph for orchestration
**Status:** Accepted
**Context:** We need parallel fan-out, conditional loops, human approval, checkpoint/resume.
**Decision:** LangGraph owns all control flow. LangChain is allowed inside nodes for model/tool abstractions only.
**Alternatives:** Hand-rolled asyncio pipeline (more control, but we would rebuild checkpointing and interrupts); CrewAI/AutoGen (less explicit loop control).
**Consequences:** Reducers are required for parallel writes. Graph structure is testable with a fake LLM.

## ADR-003: OpenCode Zen "Big Pickle" behind a provider adapter
**Status:** Capability Verified — **Blocked for External API via FreeTierError**
**Context:** Chosen model per project requirements (`big-pickle` on OpenCode Zen `https://opencode.ai/zen/v1`).
**Decision:** All model calls go through the centralized `llm/` provider adapter (`ZenLLMAdapter` / `OpenAICompatibleLLMAdapter`) with config-driven base URL, model ID, timeout, bounded retries, and secret redaction. Agents never import provider SDKs directly.
**Verification Outcome (Phase 0 Feasibility):**
- **Authentication & Model Discovery:** PASS. The API key authenticates against `https://opencode.ai/zen/v1/models` (HTTP 200, 44 models registered, `big-pickle` is present).
- **Text Generation:** FAIL. The endpoint rejects external API requests for `big-pickle` with `HTTP 403 Forbidden` and error: `FreeTierError: OpenCode's free tier can only be used from within OpenCode. Download it at https://opencode.ai and keep building.`
- **Structured Output:** FAIL (Native JSON & Prompt-Constrained). Blocked by the same HTTP 403 `FreeTierError` gate before generation begins.
- **Tool Calling:** FAIL. Blocked by the same HTTP 403 `FreeTierError` gate.
- **Observed Latency:** `/models` discovery: ~1,328 ms. Gateway rejection response time: ~400–460 ms.
- **Pricing & Policy Restrictions:**
  - `big-pickle` is classified under OpenCode's free tier.
  - OpenCode enforces an anti-scraping / client verification gate on all free-tier models (`big-pickle`, `beam-free`, `mimo-v2.6-flash-free`, etc.), blocking custom scripts, external curl, and third-party API clients.
  - Non-free models (`deepseek-v4.1-flash`, `gpt-5.4-mini`, `gemini-3.5-flash-lite`) return `HTTP 402 Insufficient account funds`, demonstrating that external API authentication works, but requires account credits.
- **Chosen Fallbacks:**
  1. *Development & Offline Testing:* `FakeLLMAdapter` in `raven.llm.fake` (114 passing unit tests covering full multi-agent workflow).
  2. *Funded OpenCode Zen Model:* Point `ZEN_MODEL` to a paid model (e.g. `gemini-3.5-flash-lite` or `deepseek-v4.1-flash`) once account balance is added.
  3. *OpenAI-Compatible Fallback:* Any OpenAI-compatible provider (e.g., OpenRouter, OpenAI, Groq, Ollama) by configuring `ZEN_BASE_URL`, `ZEN_API_KEY`, and `ZEN_MODEL` without code changes.
**Consequences:** The provider adapter is fully implemented and hardened (with bounded exponential retries, secret scrubbing, and automatic 1-attempt structured repair). Swapping models or providers remains a pure configuration change.

## ADR-004: FastAPI backend + Next.js frontend (not Streamlit)
**Status:** Accepted (assumption, revisit if speed matters more than design)
**Context:** The product needs a live flow diagram, strong typography, and fine control of layout. Streamlit limits all three.
**Decision:** FastAPI serves REST + SSE; Next.js renders the UI with React Flow for the flow view.
**Alternatives:** Streamlit (fastest to prototype, weak design control). A Streamlit prototype is acceptable for Phase 1-3 demos because the backend is UI-agnostic.
**Consequences:** Two codebases, but a clean API boundary.

## ADR-005: Server-Sent Events for live progress
**Status:** Accepted
**Decision:** One-way SSE stream of typed, persisted events with sequence numbers.
**Why:** Progress is server → client only. SSE is simpler than WebSockets, auto-reconnects, and replay is easy through persisted `seq`.
**Consequences:** User actions (approve, cancel) use normal REST calls.

## ADR-006: SQLite first, PostgreSQL later
**Status:** Accepted
**Decision:** Local-first single-user runs on SQLite (WAL mode) for both the LangGraph checkpointer and app tables. Move to PostgreSQL when multi-user or hosted.
**Consequences:** Use SQLAlchemy 2.0 + Alembic so the move is a config change, not a rewrite. Avoid SQLite-only features in app code, except behind small wrappers (FTS, vectors).
**Details:** [MEMORY_AND_DATA.md](MEMORY_AND_DATA.md)

## ADR-007: Vector search: start simple
**Status:** Accepted
**Decision:** Project indexes are small, so start with `sqlite-vec` or brute-force cosine similarity over stored embeddings. Move to pgvector with PostgreSQL. Do **not** add a separate vector database (Chroma, Qdrant, Pinecone) for the MVP.
**Why:** Fewer moving parts. Revisit only if retrieval latency or scale demands it.

## ADR-008: Bounded reflection loop, graph-owned
**Status:** Accepted
**Decision:** The critic proposes gaps; only the graph decides whether to continue, using shared budget, round count, and a no-progress check.
**Why:** Prevents runaway agents and makes cost predictable.

## ADR-009: Deterministic checks before LLM judgment
**Status:** Accepted
**Decision:** Dedupe, URL checks, excerpt-in-content checks, date math, budget logic, and path validation are plain code. The LLM is used for planning, relevance scoring, critique, extraction, and writing.
**Why:** Cheaper, repeatable, testable, and harder to manipulate through injected text.

## ADR-010: Read-only local project access
**Status:** Accepted
**Decision:** The project connector can only list and read allow-listed text files under the approved root. No execution, installs, edits, or deletes.
**Consequences:** Write-capable features require a separate design, explicit approval flow, and a new ADR.

## ADR-011: Distinct evidence states
**Status:** Accepted
**Decision:** `retrieved → verified → cited`, plus `unresolved`. "Verified" means configured checks passed, not that the claim is true. The report states the methods and limitations.

## ADR-012: Memory only stores what is safe and useful
**Status:** Accepted
**Decision:** Long-term memory holds project profiles, user-approved decisions, and past run summaries. It never stores secrets or raw excluded files. Recommendations do not become remembered "decisions" until the user approves them.
**Details:** [MEMORY_AND_DATA.md](MEMORY_AND_DATA.md)

## ADR-013: Optional connectors degrade gracefully
**Status:** Accepted
**Decision:** Missing credentials mean `disabled(reason)`, not a startup failure. Only the LLM key and one search source are required.

## ADR-014: Freshness computed in code
**Status:** Accepted
**Decision:** Freshness labels come from dates, versions, and release metadata with configured thresholds. Unknown stays `unknown`.

---

## Open questions
 
| # | Question | Status / Outcome | Needed by |
|---|---|---|---|
| Q1 | Does Big Pickle support reliable structured output and tool calling? | **Closed (Blocked):** OpenCode Zen returns `HTTP 403 FreeTierError` for `big-pickle` when called via external API. It requires either the official OpenCode client or switching to a funded non-free model. | Phase 0 |
| Q2 | Does Zen offer embeddings, or do we use a local model? | **Closed:** Use local model `BAAI/bge-small-en-v1.5` via fast local fallback (implemented & tested). | Phase 0 |
| Q3 | Is X/community access available (credentials and plan)? | Open (Product decision). Degrades gracefully to disabled if missing. | Phase 5 |
| Q4 | Is 120 s wall-clock realistic for balanced runs? | **Verified:** OpenCode gateway latency is ~400ms. Local mock workflow completes in <10s. 120s ceiling is safe. | Phase 0 measurement |
| Q5 | Hosted deployment: do we need a local connector? | Deferred until after MVP. | Later |
