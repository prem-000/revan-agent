# Architecture Decision Records (ADRs) & Implementation Decisions

## ADR-0001: Transition to RAVEN Architecture
- **Status:** Accepted
- **Context:** The system is transitioning from a 3-node academic literature reviewer into RAVEN: a multi-agent Project Intelligence & Research Agent.
- **Decision:** Restructure the orchestration graph and data models to support dual input modalities (idea/research query and read-only local project folder), explicit planning, layered memory, parallel source tunnels, normalizer, verifier, critic gap loop, and evidence-grounded synthesizer.
- **Consequences:** Replaces the legacy `research -> draft -> critique` pipeline with the 12-node RAVEN workflow defined in the implementation brief.

## ADR-0002: Groq 5-Key Resilient Provider Adapter
- **Status:** Accepted
- **Context:** Groq is the primary LLM provider. Reliability requires handling rate limits (429 with Retry-After), transient network failures, and bad/revoked keys (401/403) across up to 5 prioritized API keys without exposing secrets or evading account-level limits.
- **Decision:** Implement a centralized `GroqProviderAdapter` managing `GROQ_API_KEY_1` through `GROQ_API_KEY_5`. Maintain per-key health/cooldown timestamps, exponential backoff with jitter within a shared run budget (`MAX_LLM_CALLS_PER_RUN` and time budget), and strict secret redaction across all logs, events, and error payloads.
- **Consequences:** All agent nodes invoke the LLM through the adapter. Individual agents never import provider SDKs directly.

## ADR-0003: Pydantic Typed Data Exchange Between Agents
- **Status:** Accepted
- **Context:** The implementation brief mandates typed Pydantic models for data exchanged across LangGraph agent nodes, disallowing unvalidated raw dictionaries.
- **Decision:** Model all artifacts (ResearchPlan, EvidenceItem, SourceTunnelResult, CritiqueReport, GapTask, FinalReport) as Pydantic models stored within a strongly-typed `RavenState`.

## ADR-0004: Layered Memory Architecture & Read-Only Project Inspector
- **Status:** Accepted
- **Context:** Memory must avoid unbounded context growth and never execute code or persist secrets. Local project folder inspection must be read-only and path-scoped.
- **Decision:** Implement 6-layer memory (Working, Episodic, Project, Decision, Evidence Cache with TTL, Preference). Implement `ProjectInspector` with path traversal prevention, symlink validation, file-type allowlists, and read-only AST/structural parsing.

## ADR-0005: Mermaid-Only Workflow Visualization
- **Status:** Accepted
- **Context:** The product requirement is a clear, live agent workflow diagram rather than an interactive citation force simulation.
- **Decision:** Replace `react-force-graph-2d` for workflow rendering with Mermaid flowcharts generated dynamically from backend execution events. Sanitize and encode node text to prevent injection.

## ADR-0006: Hybrid Browser & HTTP Resource Extraction with SSRF Protection
- **Status:** Accepted
- **Context:** High-quality research requires inspecting actual web resources rather than relying merely on search snippets, while protecting backend infrastructure from SSRF and excessive memory usage.
- **Decision:** Implement `BrowserInspector` combining fast safe HTTP fetching with Playwright headless browser fallback for JavaScript-rendered sites. Enforce strict SSRF protection: scheme allowlist (`http/https`), private IP and cloud metadata rejection (`127.0.0.1`, `169.254.169.254`, `10.0.0.0/8`, `192.168.0.0/16`), DNS address validation, and a 5MB response cap.

## ADR-0007: Deep GitHub Repository Extraction & Comparison
- **Status:** Accepted
- **Context:** Evaluating open-source tools requires inspecting repository readmes, latest releases, commit dates, open issues, and licenses, rather than ranking by stars alone.
- **Decision:** Enhance `GitHubTunnel` to extract README architecture sections and latest release metadata. In `Synthesizer`, construct formatted markdown Repository Comparison tables comparing language, activity, license, and suitability.

## ADR-0008: Bounded Content Store & SQLite FTS5 Memory
- **Status:** Accepted
- **Context:** Full page and README contents must be retained for reference without bloating LangGraph graph states or database JSON files.
- **Decision:** Implement `BoundedContentStore` with a 50MB disk limit and LRU cleanup. Augment the 6-layer memory store with SQLite FTS5 virtual tables (`raven_memory.db`) for fast full-text search and BM25 ranking over episodic runs and evidence cache.

## ADR-0009: LangSmith Tracing & Evaluator Metrics System
- **Status:** Accepted
- **Context:** Continuous measurement and traceability require logging diagnostics and evaluating run quality against a fixed dataset of realistic queries.
- **Decision:** Implement `RavenEvaluator` with 10 diagnostic quality metrics (coverage, citation validity, citation support, relevance, source diversity, extraction success, correction gain, cost/latency) and optional redacted LangSmith tracing.

## ADR-0010: Measured Cross-Run Improvement Proposals
- **Status:** Accepted
- **Context:** Continuous agent learning must not allow arbitrary autonomous code rewrites that risk regressions or safety issues.
- **Decision:** Implement `CrossRunLearningEngine` to aggregate recurring failure patterns, draft versioned proposals for behavioral parameters, test them against the fixed benchmark dataset, and require verified gains before adoption.

## ADR-0011: Content-First Editorial UI & Graph Removal from Main Reading Flow
- **Status:** Accepted
- **Context:** User feedback and product requirements revealed that displaying a title, generic summary ("Synthesized from X verified items"), and links alongside a giant Mermaid graph failed to answer user questions or provide reading space.
- **Decision:**
  1. Remove the large Mermaid diagram entirely from the main completed report view. Confine workflow progress visualization exclusively to the loading phase.
  2. Implement a responsive editorial research workspace (`--raven-bg: #0B1020`, `--raven-surface: #111827`, `--raven-accent: #8B7CFF`) using full readable width.
  3. Reorganize report hierarchy: Direct Answer callout -> Key Grounded Findings -> Detailed Explanation (Markdown with code, complexity tables, steps) -> Practical Guidance -> Limitations & Uncertainties -> Secondary Sources & Evidence drawer on the side/bottom -> Collapsible Technical Run Diagnostics drawer -> Unified follow-up input.

## ADR-0012: Groq-Only Structured Synthesis with Bounded Context & Zero-Retry Instant Failover
- **Status:** Accepted
- **Context:** Synthesis must run strictly on Groq without silent provider fallbacks. Large prompt payloads caused Groq HTTP 413, and default LangChain retries caused 15s-27s internal thread blocking on rate limits.
- **Decision:**
  1. Strict Groq-only inference contract with model negotiation (`openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`).
  2. Implement `SynthesizerLLMOutput` concise Pydantic schema for generation, bounding evidence excerpts in the prompt to top 6 items (350 chars each) to prevent HTTP 413 Payload Too Large.
  3. Set `max_retries: 0` inside `ChatOpenAI` and delegate all retry/backoff logic to `GroqProviderAdapter`, which uses round-robin failover across all 5 configured keys (`GROQ_API_KEY_1..5`) with per-slot cooldowns.

## ADR-0013: Tunnel Resilience, Browser Graceful Fallback, Trusted TLS & Research Job Decoupling
- **Status:** Accepted
- **Context:** Production runs encountered unexpected terminations and tunnel errors under Windows and concurrent execution:
  1. Windows `_WindowsSelectorEventLoop` under Uvicorn `--reload` lacks subprocess support, causing `NotImplementedError` during Playwright launch.
  2. Tunnel dispatcher `asyncio.gather(..., return_exceptions=False)` allowed single tunnel failures to abandon sibling tasks and trigger unretrieved task exceptions.
  3. arXiv queries failed with `SSLCertVerificationError: CERTIFICATE_VERIFY_FAILED` on environments lacking root CA bundles.
  4. SSE streaming executions ran directly in client response generators, causing research runs to abort whenever clients disconnected.
- **Decision:**
  1. In `BrowserInspector`, detect event loop subprocess capabilities before launching Playwright. Gracefully disable browser extraction when running under Windows `SelectorEventLoop` or when Playwright is unavailable, caching the availability state and falling back cleanly to safe HTTP extraction while preserving any partially extracted content.
  2. Provide trusted CA certificate validation via `get_trusted_ssl_context()` using `certifi` (or `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`) with strictly enforced TLS verification across all HTTP and arXiv requests. Isolate any TLS errors to the affected source tunnel.
  3. In `TunnelDispatcher`, supervise all concurrent tunnel and plan executions using `asyncio.gather(*tasks, return_exceptions=True)`. Catch exceptions, classify them into structured `SourceTunnelResult` objects, preserve sibling tunnel evidence, and cleanly propagate `asyncio.CancelledError`.
  4. Introduce `ResearchJobManager` to decouple research run lifecycles from SSE client connection lifecycles. Research tasks run in supervised background tasks; client disconnects do not cancel the run; explicit cancellation is supported via `POST /runs/{job_id}/cancel`; and a FastAPI `lifespan` handler ensures clean task cancellation and resource disposal on server shutdown.

