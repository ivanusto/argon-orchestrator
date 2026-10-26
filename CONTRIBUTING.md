# Contributing

## Setup

- Python 3.11+ for the tooling (tests).
- The hooks themselves run on the Python 3.8+ standard library only. Do not add dependencies or newer syntax to `scripts/`.
- Install `ruff` and `vermin` for the lint checks, and `agy` for plugin validation.

## Checks CI runs

```bash
python3 -m unittest discover -s tests
ruff check .
vermin -t=3.8- --no-tips --violations scripts
agy plugin validate . --strict
```

## Code layout

- `scripts/enforce-orchestrator.py`: hook entry point (modes `session-start`, `pre-invocation`, `pre-tool-use`, `post-tool-use`, `stop`).
- `scripts/argon_hook/`: `common`, `models`, `routing`, `judge`, `report`, `state`, `guards`, `tools`, `handlers`.
- `agents/`, `rules/AGENTS.md`, `skills/orchestrate/SKILL.md`: the policy the model reads.

## Routing changes

The routing engine is ported verbatim from mitosis-orchestrator 2.3.0. Keep it identical unless the change lands in both repositories.

- Add labelled cases to `tests/routing_cases.jsonl`, in both directions: prompts that must reach the scout and prompts that must reach the worker.
- `tests/test_routing_eval.py` enforces accuracy >= 0.95, cost errors <= 0.05 and zero worker tasks sent to the scout.
- Never tune rules on a holdout set you have already scored.
- Prove parity with mitosis: `python3 scripts/routing-parity.py <mitosis_repo>` must report 0 differences.

## Permission behaviour

Verified live on agy 1.4.6 (2026-10-23): a hook answer of `{}` denies the call, and `{"decision":"allow"}` means "no objection" while agy still shows its own permission prompt for commands and file writes. Hooks must therefore return an explicit allow when they have no opinion, and must never try to bypass agy prompts. Only `AGY_ARGON_AUTO_ALLOW=1` grants permission overrides, and only inside the workspace.

## Commit style

Conventional prefixes: `feat`, `fix(routing)`, `test`, `docs`. One logical change per commit.

## Documentation

[README.md](./README.md) and [README.zh-TW.md](./README.zh-TW.md) change together. Do not use em dashes in the Chinese text.

## Security

Report vulnerabilities privately, see [SECURITY.md](./SECURITY.md).
