# AutoWeb Domain Context

## Terms

- **Task Run** — one durable execution of a user's natural-language web task. It owns a thread id, Task Contract, verified progress, browser/CLI session identity, and terminal status.
- **Task Contract** — the versioned, deterministic interpretation of the user's requested fields, counts, navigation mode, filters, details, and stop conditions.
- **Verified Step** — an action whose result has passed Verifier checks. Only verified steps may advance durable Task Run progress.
- **Run Manifest** — the small, queryable record for a Task Run: task, status, CLI session, current URL, item count, completed pages, and last verified action key.
- **Run Trace** — append-only evidence for node execution, model usage, browser actions, verification, retries, latency, and cost.
- **Page Model** — the stable semantic representation of a rendered page used for snapshots, element identity, diffing, and ref rebinding.
- **Action Memory** — reusable, evidence-bearing knowledge of previously successful browser actions and extraction plans.
- **Site Policy** — per-domain authorization, robots, pacing, request-budget, and blocking-signal rules applied before browser work.

