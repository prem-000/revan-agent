# RESOURCE_POLICY.md

All limits are **starting defaults**, configurable via environment, and should be tuned using the evaluation suite. One shared `Budget` object covers every agent and every retry.

## 1. Run limits

| Limit | Quick | Balanced (default) | Deep |
|---|---|---|---|
| Research rounds (including first) | 1 | 3 | 4 |
| Concurrent tunnel tasks | 3 | 5 | 6 |
| Transient retries per failed task | 0 | 1 | 1 |
| Total LLM calls per run | 10 | 25 | 50 |
| Report repair attempts | 1 | 1 | 1 |
| Wall-clock time | 60 s | 120 s | 300 s |

The balanced wall-clock default (120 s) may be tight with a slower model. Measure real latency in Phase 0 and adjust before locking it in.

### Example LLM call budget (balanced)

| Stage | Calls |
|---|---|
| Analyzer | 1 |
| Round 1: planner 1 + 5 tunnel extractions + verifier batch 1 + critic 1 | 8 |
| Round 2 (follow-up, 3 tunnels): 1 + 3 + 1 + 1 | 6 |
| Round 3 (follow-up, 3 tunnels): 1 + 3 + 1 + 1 | 6 |
| Synthesizer + 1 repair | 2 |
| **Total** | **23** (limit 25, two spare) |

## 2. Enforcement

- A `BudgetGate` wraps the LLM adapter and the tunnel runner. Calls beyond the limit are refused, not queued.
- Counters are additive and merged through reducers so parallel workers cannot overwrite each other.
- The wall-clock timer starts at run start and **excludes time waiting for user plan approval**.
- Workers cannot raise limits. Limits are read-only inside agents.
- A usage snapshot is emitted as a `budget.update` event after every node.

## 3. Stop reasons

| Reason | Meaning |
|---|---|
| `sufficient` | Critic found no material gaps |
| `max_rounds` | Round limit reached |
| `budget_exhausted` | LLM-call or time budget reached |
| `no_progress` | A follow-up round added no new verified evidence |
| `cancelled` | User cancelled |
| `failed` | Unrecoverable error (state and partial evidence still saved) |

On any non-`sufficient` stop, the synthesizer still produces the best supported partial report and lists unresolved objectives.

## 4. Per-node token caps (starting values)

| Node | Max output tokens | Max input context |
|---|---|---|
| Analyzer | 1,500 | Project summary excerpts, capped by file/size limits |
| Planner | 2,000 | Brief + gaps |
| Tunnel extraction | 600 per source | One fetched page, truncated to a character limit |
| Verifier (batched) | 1,200 | Excerpts only, never full pages |
| Critic | 1,800 | Evidence excerpts + objectives |
| Synthesizer | 4,000 | Evidence ledger (excerpts + metadata) |

Truncation is explicit and recorded, so the report never claims to have read more than it did.

## 5. Source rate limits and politeness

| Source | Policy |
|---|---|
| Tavily | Respect plan quota. Max results per query is configurable. |
| GitHub | Use a token for higher limits. Honour `X-RateLimit-*` headers. Prefer conditional requests (ETag). |
| Semantic Scholar / paper APIs | Use API key if available. Back off on 429. |
| X | Only with valid credentials and plan. Otherwise `disabled`. |
| Any HTTP fetch | Timeout 10 s, max 2 MB body, honour robots and `Retry-After`, set a clear User-Agent. |

## 6. Cache policy (starting TTLs)

| Item | TTL | Invalidation |
|---|---|---|
| Web search results | 6 h | Query hash |
| Fetched web pages | 24 h | URL + ETag / content hash |
| Official docs | 24 h | URL + version |
| GitHub release/repo metadata | 1 h | ETag |
| Paper metadata | 7 d | DOI |
| Community posts | 1 h | Post ID |
| Local file index | Until file hash/mtime changes | Per-file hash |
| LLM outputs | Not cached across runs by default | n/a |

User can force **refresh** to bypass the cache. The report always shows `checked_at`, so cached data is never presented as newly fetched.

## 7. Freshness thresholds (starting values)

| Source | fresh | aging | stale |
|---|---|---|---|
| GitHub (last commit/release) | < 6 mo | 6-18 mo | > 18 mo or archived |
| Official docs (last updated / version) | Matches latest version | 1 minor/major behind | 2+ behind or deprecated |
| Web articles | < 12 mo | 12-24 mo | > 24 mo |
| Community posts | < 3 mo | 3-12 mo | > 12 mo |
| Papers | Not age-penalised, but flagged if > 3 yrs in fast-moving topics | | |

Missing dates produce `unknown`, never a guess.

## 8. Project indexing limits

| Limit | Default |
|---|---|
| Max files per initial scan | 2,000 |
| Max file size indexed | 256 KB |
| Max total indexed text | 20 MB |
| Included types | Source, config, docs, manifests (allow-list) |
| Always excluded | `.env*`, keys, certs, `.git/`, `node_modules/`, build output, caches, binaries |
| Symlinks | Not followed outside the approved root |

## 9. Degradation order when constrained

1. Drop lowest-priority objectives' follow-ups.
2. Reduce results per tunnel.
3. Skip optional LLM relevance scoring (use deterministic checks only).
4. Stop and synthesize a partial report with an explicit unresolved list.

Never skip verification or citation validation to save budget.
