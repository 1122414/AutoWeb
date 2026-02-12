# Grade Plan Signature (Draft)

## Goal
Provide a future-ready `action_signature` gate for `dom_cache` to reduce false hits on same-page but different-operation tasks.

## Why Deferred
- Current user tasks are mostly Chinese free-form natural language.
- Premature strict signature gating may reduce useful recalls.
- We keep this as a staged plan and do not enable in runtime for now.

## Signature Design (Future)
1. Input Sources
- `user_task`
- `plan` (if available)
- `locator_suggestions` (optional boost)

2. Extraction
- Action verbs: `click/input/search/open/extract/paginate/download/submit/sort/filter`
- Target nouns: `table/list/detail/form/button/link/input`
- Normalize synonyms, lowercase, deduplicate, stable sort.

3. Output
- `action_signature`: pipe-separated tokens, e.g. `extract|table|paginate`
- `action_family`: coarse label, e.g. `extract` / `navigate` / `form`

## Storage Changes (Future)
- Add scalar fields in `dom_cache`:
  - `action_signature` (varchar)
  - `action_family` (varchar, optional)

## Retrieval Gate (Future)
Two-stage filter:
1. Existing hybrid recall (`url/dom/task`) + task hard threshold.
2. Signature overlap gate:
- overlap ratio >= `DOM_CACHE_ACTION_MIN_OVERLAP` (recommended 0.5)
- if signature missing, degrade to task gate only.

## Config (Future)
- `DOM_CACHE_ACTION_GATE_ENABLED=true/false`
- `DOM_CACHE_ACTION_MIN_OVERLAP=0.5`
- `DOM_CACHE_ACTION_REQUIRE_NONEMPTY=true/false`

## Rollout
1. Shadow mode logs only (no blocking).
2. Soft gate on low-risk pages.
3. Full gate after hit quality validation.
