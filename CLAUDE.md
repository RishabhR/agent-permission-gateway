# Agent Permission Gateway

Weekend project. A permission gateway that sits between an LLM agent and its tools,
plus an eval comparing five permission designs (standing, per-action x2, session-scoped,
task-scoped) on safety, task success, and approval-prompt cost.

The work plan is in `PLAN.md`. Follow milestones in order and STOP at each CHECKPOINT.

## Source-of-truth files (do not change semantics without asking)
- `policy/grant.schema.json`: grant, manifest, decision formats
- `policy/policies.yaml`: the five designs
- `eval/fixtures.yaml`: the mock world
- `eval/tasks.yaml`: tasks, ground truth, matcher syntax

## Rules
- Python 3.11+, managed with `uv`. Deps: anthropic, mcp, pydantic>=2, pyyaml, jsonschema, pytest. Ask before adding anything else.
- Mock world only. Never connect to real email, calendar, or payment accounts.
- Deterministic tools: fixed clock from fixtures, no randomness, no network inside tools.
- The policy engine (`gateway/policy.py`) is a pure function with no I/O. Test-first.
- The agent's system prompt, model, and tool definitions are identical across all policies. Only the gateway changes.
- PROVENANCE RULE: grants are minted only from user-authored text. The planner never sees tool output or inbox content. A proposed grant whose `user_text_excerpt` is not an exact substring of the user message is dropped and logged.
- Every tool call produces exactly one Decision line in the audit log, including denials.
- Tool names use underscores (API tool names cannot contain dots).
- Model IDs come from env: `AGENT_MODEL` (default `claude-sonnet-5-5`), `PLANNER_MODEL` (default same).
- Keep stdout clean in the MCP server (stdio transport); log to stderr.

## Commands
- `uv run pytest -q`: tests
- `uv run python -m harness.run_eval --policies all --repeats 1`: full eval
- `uv run python -m harness.run_eval --policies task_scoped --tasks T15`: single slice
- `uv run python -m harness.score results/runs.jsonl`: results table

## Style
- Small modules, type hints, pydantic models mirroring the JSON schema.
- Prefer clear failure over silent fallback, especially in matching and scoring.
