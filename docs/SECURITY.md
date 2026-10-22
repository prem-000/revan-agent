# SECURITY.md

RAVEN reads local files and fetches untrusted web content, so the threat model matters.

## 1. Principles

1. Local access is opt-in, scoped to one approved folder, and **read-only**.
2. Project files and all external content are **untrusted data**, never instructions.
3. Workers cannot change limits, routing, or verification status.
4. No source is shown as verified unless configured checks passed.
5. No fabricated citations.
6. Secrets stay in environment config or a secret manager, never in code, logs, events, or reports.
7. User approval is required before recommendations become decisions or before any future write-capable action.

## 2. Threats and mitigations

| Threat | Mitigation |
|---|---|
| **Path traversal** (`../`, absolute paths) | Resolve to real path, require it to be inside the approved root, reject otherwise. Single choke point: `project/guard.py`. |
| **Symlink escape** | Do not follow symlinks that resolve outside the root. Check after resolution. |
| **Secret exposure from project** | Default-deny list: `.env*`, `*.pem`, `*.key`, `id_rsa*`, credential/token files, cloud config. Plus a content scanner for key-like patterns before indexing. Excluded files are never read, embedded, or sent to the model. |
| **Prompt injection** (web page, README, code comment) | Delimit retrieved text as data. Keep system rules outside. Deterministic checks decide verification and routing. Injection test suite in EVALUATION.md. |
| **SSRF through fetch tools** | Block private, loopback, link-local, and metadata IP ranges. Only http(s). Re-check after redirects. Limit redirects, size, and time. |
| **Malicious/huge content** | 2 MB body cap, 10 s timeout, content-type allow-list, safe HTML-to-text extraction. |
| **Runaway cost or loops** | Central budget gate, round cap, no-progress stop. |
| **Secret leakage in logs/errors** | Redaction filter for keys and tokens in logs, exceptions, SSE events, and reports. |
| **Unauthorised API use** | Local-first binds to `127.0.0.1`. Any hosted mode needs authentication. CORS restricted to the frontend origin. |
| **Arbitrary remote paths** | `project_root` is accepted only from the trusted local connector after user selection, never from an unauthenticated API body. |
| **Supply chain** | Pin dependencies, lockfiles, dependency audit in CI. |
| **Report XSS** | Render Markdown with sanitisation. Never inject raw HTML from sources. |
| **Data over-collection** | Memory stores only what is needed. User can view and delete it. |

## 3. Project connector rules

- Allow-list of file types. Size and count limits (see RESOURCE_POLICY.md).
- Never executes, imports, builds, or installs anything from the project.
- Returns relative paths and line ranges only, never absolute paths, in evidence.
- Shows the user exactly which files were consulted.

## 4. Handling secrets found

If the scanner finds a key-like string in an otherwise allowed file, skip that file or redact the match, log a **redacted** warning, and tell the user to rotate it if it was committed. Do not echo the value anywhere.

## 5. Provider data handling

Content sent to the LLM provider leaves the machine. Tell the user this in the UI before the first project-mode run, and let them review the files in the project context. Verify the provider's data-retention terms before sending proprietary code.

## 6. Reporting vulnerabilities

Document a private contact in the repository before public release.
