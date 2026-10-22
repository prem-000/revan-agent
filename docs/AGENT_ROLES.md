# AGENT_ROLES.md

Each agent has **one job**, typed inputs and outputs, and explicit limits. Anything that can be done deterministically is done in code, not by the model.

## Summary

| # | Agent | LLM? | Runs | Output |
|---|---|---|---|---|
| 1 | Analyzer | Yes | Once | `ProjectContext` or `IdeaBrief` |
| 2 | Planner | Yes | Once + gap rounds | `ResearchPlan` |
| 3 | Tunnel workers | Light | Parallel per round | `TunnelResult` |
| 4 | Normalizer | No | Each round | Deduped evidence |
| 5 | Verifier | Mostly no | Each round | `VerificationReport` |
| 6 | Critic | Yes | Each round | `CriticReport` |
| 7 | Synthesizer | Yes | Once | `Report` |
| 8 | Orchestrator | No (graph code) | Whole run | Routing, budget, stop reason |

---

## 1. Analyzer

**Purpose:** Understand what the user gave us.

- **Idea mode:** extract goal, constraints, target users, assumptions, unknowns.
- **Project mode:** use the project connector (read-only) to inventory files, detect language, frameworks, dependencies with versions, entry points, then summarize relevant excerpts with paths and line ranges.

**Input:** query, optional `project_root`.
**Output:** `IdeaBrief | ProjectContext` (unknowns explicitly marked).
**Limits:** 1 LLM call. Reads only files allowed by `project/guard.py`. Never executes code.

## 2. Planner

**Purpose:** Turn the brief into research objectives. Also creates **targeted follow-up tasks** in later rounds.

Each objective has: stable `objective_id`, research question, rationale, priority, suggested tunnels, expected evidence, acceptance criteria.
Each plan also lists **feature and error checks** to run, for example "verify the chosen library supports X", "check for deprecations", "check license compatibility".

**Input:** brief or context, previous critic gaps (rounds 2+).
**Output:** `ResearchPlan` (structured output).
**Limits:** 1 call per round. Cannot add tunnels that are disabled. Cannot change approved objectives, only add follow-up tasks.
**Approval gate:** the user can edit or approve before any expensive research starts.

## 3. Tunnel workers

Logical source adapters sharing one `Tunnel` interface. They run **concurrently** and never control the loop.

| Tunnel | Looks for | Freshness signals |
|---|---|---|
| GitHub | Repos, examples, issues, releases, license, activity | Latest release, last commit, archived |
| Official docs | API refs, guides, migration notes, changelogs | Doc version, last updated |
| Research papers | Scholarly work, abstracts, DOIs | Publication date, newer versions |
| Web | Technical articles, comparisons | Publish/modified date |
| Community (X, forums) | Firsthand experience, announcements | Post timestamp |
| Local project | Relevant files, excerpts, dependency versions | File mtime, version vs. latest |

**Output per run:** `TunnelResult` with `EvidenceRecord`s (source URL or file path + lines, excerpt, dates, `checked_at`).
**Rules:**
- Return structured data, never only free text.
- Report `disabled(reason)` when credentials are missing.
- Treat all retrieved content as untrusted data.
- Respect robots, auth, rate limits, and API terms. Do not assume X access without credentials and plan.
- A search snippet alone is not conclusive proof for a high-impact claim; fetch the page where it matters.
- One optional LLM call to extract a claim and excerpt from fetched content, counted in the shared budget.

## 4. Normalizer (deterministic)

Standardises records, canonicalises URLs, strips tracking parameters, removes exact and near-duplicate excerpts, assigns stable evidence IDs.

## 5. Verifier

**Purpose:** Decide what can be trusted and how fresh it is.

Checks (in order, cheap first):
1. **Attribution:** source URL or file+lines present and well-formed.
2. **Reachability:** URL resolves (HEAD/GET) or evidence was fetched this run.
3. **Excerpt quality:** non-empty, long enough, actually appears in the fetched content.
4. **Relevance:** one **batched** LLM call scores relevance to each objective.
5. **Freshness:** computed from dates and versions (`fresh | aging | stale | unknown`).

**Output:** per-record `verification_status` (`retrieved` → `verified` or `failed`) with the method used.
**Rule:** passing these checks means "configured checks passed", not "the claim is true". The report says so.

## 6. Critic (gap, contradiction, risk, and error evaluator)

**Purpose:** Re-check the work. This is the "evaluate and recheck" step.

Produces:
- **Coverage:** per objective: `covered | weak | missing`, with the evidence IDs behind the score.
- **Contradictions:** conflicting claims across sources, both kept visible.
- **Feature risks:** unsupported features, deprecated APIs, version incompatibilities, license problems, abandoned dependencies, security advisories found in evidence.
- **Project errors (project mode):** places where the existing code or dependency versions conflict with what the evidence says, with file path and line range.
- **Gap list:** only objectives that are weak or missing, with a suggested targeted query and tunnel.
- **Decision:** `sufficient | continue`.

**Limits:** 1 call per round. Every finding must point at evidence IDs. A risk with no evidence is labelled an assumption.

## 7. Synthesizer

**Purpose:** Write the final report from the evidence ledger only.

Produces: summary, findings with citations, freshness badges, technology comparison, proposed architecture, implementation roadmap, risks, **unresolved questions**, project files consulted, verification method and limitations.

**Rules:**
- Every citation must resolve to an `EvidenceRecord` gathered in this run. A post-check enforces it.
- Separate **fact** (cited), **inference**, and **recommendation**.
- Recommendations are suggestions. They are not decisions until the user approves them.
- If the budget ran out, say which objectives remain unresolved.
- One repair attempt if citation validation fails.

## 8. Orchestrator (graph code, not a model)

Owns: state, routing, fan-out, concurrency cap, shared budget, retries, stop reasons, checkpointing, event emission. Workers cannot change any of these.

## Shared agent contract

- Typed Pydantic input/output only.
- No direct provider imports; use the LLM adapter.
- Every LLM call goes through the budget gate.
- Errors are redacted and returned as structured failures.
- Prompts are versioned files; injected content is clearly delimited as data.
