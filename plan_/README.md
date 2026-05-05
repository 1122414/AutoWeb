# Plan Archive

This directory contains chronological implementation plans for AutoWeb.

## Structure

```
plan_/
  README.md          ← you are here
  4.29/              ← initial dp_cli integration
  5.2/               ← node upgrade planning
  5.3/               ← dp_cli observer + target selector
  5.4/               ← verifier detail policy + main loop closure
  5.5/               ← current: project structure refactor
```

## How to use

- Plans are **historical task plans**, not source of truth for current behavior.
- The newest active plan is usually in the highest date/version directory.
- Not all old plans describe current behavior — some describe ideas that were revised or rejected.
- For current architecture, see `docs/architecture/` and project `AGENTS.md` files.

## Warning

Plans describe what was INTENDED at a point in time, not necessarily what was EXECUTED.
Always verify against the actual codebase.
