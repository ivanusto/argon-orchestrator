# Just agy Orchestrator (`just-agy-orchestrator`)

[繁體中文說明 (README.zh-TW.md)](./README.zh-TW.md)

An Antigravity (`agy`) only plugin. It keeps a frontier model as the primary orchestrator and routes every subagent to `flash` or `flash_lite` with a confidence-scored rule engine, guards the risky tool calls, and enforces a bounded four-header subagent report.

Supported primary models (as offered by agy 1.4.6 and later): **Gemini 4 Argon (Medium / High)**, **Gemini 4 Carbon (Medium / High)** (agy 1.5.5+, ADC sign-in only; the default stays Argon), **Gemini 4 Hydrogen (High / XHigh)** and **Orchestrator (Carbon)** / **Orchestrator (Argon)** (agy 1.5.7+), **Claude Opus 5.5**, **Claude Sonnet 5.5**. The agy Orchestrator splits planning (Carbon/Argon) and execution (Hydrogen) internally, so this plugin's dispatch stacks on top of it. After planning ends (`exit_plan_mode`) Orchestrator (Carbon) hands execution to an internal executor whose hook payloads report `modelName` as `gemini-flash-worker-agy`; the plugin treats it as part of the Orchestrator and does not issue the non-frontier warning. Argon Low, Carbon Low, Hydrogen Low/Medium, Gemini 3.8 Flash and GPT-OSS 120B are warned about as non-frontier.

---

## Architecture and routing policy

| Tier | Model | Role |
| :--- | :--- | :--- |
| **Primary orchestrator** | Gemini 4 Argon or Carbon (Medium / High), Gemini 4 Hydrogen (High / XHigh), Orchestrator (Carbon / Argon), Claude Opus 5.5, Claude Sonnet 5.5 | Architecture, planning, cross-cutting refactors, security review, final synthesis. |
| **Tier 1 (`flash-worker`)** | `flash` | Multi-file research, web and documentation work, writing and running tests, scoped edits and debugging. |
| **Tier 2 (`flash-lite-scout`)** | `flash_lite` | Read-only lookups: files, symbols, grep/glob, git and log checks, single-file inspection. |

---

## Components

- **[`rules/AGENTS.md`](./rules/AGENTS.md)**: always-on policy: routing matrix, invocation rules, the four-header report contract, branch cleanup.
- **[`hooks.json`](./hooks.json) and [`scripts/enforce-orchestrator.py`](./scripts/enforce-orchestrator.py)** (Python 3.8+, standard library only). The logic lives in `scripts/just_agy_hook/` (`common`, `models`, `routing`, `judge`, `report`, `state`, `guards`, `tools`, `handlers`).
- **[`agents/`](./agents/)**: `flash-lite-scout` (`flash_lite`, read-only) and `flash-worker` (`flash`, edits only inside the prompt's file allowlist).
- **[`skills/orchestrate/SKILL.md`](./skills/orchestrate/SKILL.md)**: the `/orchestrate` four-phase workflow (scout recon, primary plan, worker execution, primary verification).

### Hooks

| Hook | What it does |
| :--- | :--- |
| `SessionStart` | Checks the primary model in `~/.gemini/antigravity-cli/settings.json` and warns if it is not on the list above. Warns when `mitosis-orchestrator` is also enabled. Rewrites settings only with `JUST_AGY_PIN_SETTINGS=1`. |
| `PreInvocation` | Re-checks the live model each turn (catches UI model switches) and resets per-turn tracking. |
| `PreToolUse` `invoke_subagent` | Routes every subagent to `flash` or `flash_lite`, appends the REPORT FORMAT block to prompts missing the four headers, applies scout escalation, and denies when the circuit breaker is tripped. agy 1.4.6 sends one flat `invoke_subagent` call per subagent; both forms are handled. |
| `PreToolUse` `send_message` | Denies code-change or multi-step follow-ups to a `flash_lite` recipient. Validates a subagent's own report to its parent and denies an invalid one once so it resends. |
| `PreToolUse` `run_workflow` / `define_subagent` | Denies `agent(...)` calls without `model="flash"` or `"flash_lite"`; forces `enable_subagent_tools=false`. |
| `PreToolUse` scout layer | `flash-lite-scout` subagents are limited to read-only tools and commands. |
| `PreToolUse` hygiene and fast path | Blocks direct primary reads of `.log` and `transcript.jsonl` (files up to 16 KiB are allowed); caps direct edits (default 3 files per turn) and edit-then-test loops. |
| `PostToolUse` | Pairs subagents with metadata and tracks failure streaks for the circuit breaker. |
| `Stop` | Asks once to merge or discard and kill live `Workspace:"branch"` workers. |

State is guarded by a per-session `flock` that fails open after a timeout, and saved atomically.

### Routing

The engine is ported verbatim from mitosis-orchestrator 2.3.0. Each decision carries a confidence. Strong signals (tests, debugging, tracing, mutation verbs) beat lookup words, Chinese rules are included, a `scout-unproven` rule requires positive read-only evidence before a task may go to the scout, and a worker-lookup downgrade sends pure lookups from `flash` to `flash_lite`. An optional judge can arbitrate low-confidence cases.

`tests/routing_cases.jsonl` (855 cases) and `tests/routing_holdout*.jsonl` (v1 to v8, 40 each) back `tests/test_routing_eval.py`: accuracy >= 0.95, cost errors <= 0.05, zero worker tasks sent to the scout. `scripts/routing-parity.py <mitosis_repo>` proves 0 differences against mitosis.

The agy `Role` is only a label (for example "Add Scout") and does not affect routing; the keyword rules read the `Prompt` alone, so state edits and lookups in the Prompt.

### Escalation and report enforcement

- **Escalation.** If a scout reports `FAILED` or `BLOCKED`, the next scout dispatch is escalated to `flash-worker` once, provided it happens within 3 dispatches or 30 minutes. Disable with `JUST_AGY_ESCALATE=0`.
- **Report format.** Subagent prompts lacking the four headers get the REPORT FORMAT block appended (`JUST_AGY_APPEND_REPORT_FORMAT=0` disables).
- **Report enforcement.** A subagent's `send_message` to its parent is validated against the contract; an invalid report is denied once so the subagent resends (`JUST_AGY_ENFORCE_REPORT=0` disables).

**Note on the agy 1.5 toolset.** Verified on agy 1.5.5: `find_by_name`, `grep_search` and `list_dir` are retired, so a scout has only `read_file`, `send_message` and `manage_task`. Give scouts exact file paths; searches, listings, `git` and log checks, test runs, web research and edits go to `flash-worker`. The agent tool lists keep the old names (`view_file`, `replace_file_content`, `write_to_file`), which agy 1.5 resolves as aliases; do not list an old name next to its new name, because the agent then fails to build.

**Note on `inheritCustomizations`.** The bundled agents set `inheritCustomizations: false`, so agy does not run plugin hooks inside `flash-lite-scout` or `flash-worker` subagents. Subagent-side report rejection (deny-once on `send_message`) and the scout read-only layer therefore only fire for subagent types that inherit customizations (built-in research/self). For the bundled types, the primary-side message scan (invalid-report ephemeral notice) and scout-failure escalation do the work, and agy itself enforces the agent tool lists (verified: a scout given an edit task could not edit).

The contract is `STATUS`, `FILES_CHANGED`, `VERIFICATION`, `NOTES`; up to 10 bullets under `NOTES`; at most 1,600 characters (the `file://` target of a markdown link such as `[calc.py](file:///abs/calc.py#L1-L2)` is not counted, only its text); a `file://` log URI when `BLOCKED` or `FAILED`.

---

## Permission behaviour

Verified live on agy 1.4.6 (2026-10-23):

- A hook answer of `{}` is a **deny**.
- `{"decision":"allow"}` means "no objection"; agy still shows its own permission prompt for commands and file writes.
- The plugin therefore returns an explicit allow when it has no opinion and never bypasses agy prompts.
- `JUST_AGY_AUTO_ALLOW=1` is the only path that grants `permissionOverrides`, and only inside the workspace. Outside paths, privilege escalation, root deletes, force pushes and shell-piped downloads are answered `ask`.

In headless `agy -p` mode agy soft-denies anything that needs a prompt regardless of hooks.

---

## Environment variables

The settings use the `JUST_AGY_*` prefix. The old `AGY_ARGON_*` names (and `AGY_ALLOW_NON_ARGON_PRIMARY`) still work; when both are set, the `JUST_AGY_*` value wins.

| Variable | Effect |
| :--- | :--- |
| `JUST_AGY_PIN_SETTINGS=1` | Rewrite `settings.json` to the primary model at session start. |
| `JUST_AGY_PRIMARY_MODEL` | Model used when pinning (default `Gemini 4 Argon (High)`). |
| `JUST_AGY_ALLOW_NON_PRIMARY=1` | Silence primary-model warnings. |
| `JUST_AGY_AUTO_ALLOW=1` | Grant `permissionOverrides` for in-workspace tools. |
| `JUST_AGY_MAX_DIRECT_EDITS=<int>` | Distinct workspace files the primary may edit per turn (default 3). |
| `JUST_AGY_ALLOW_DIRECT_LOGS=1` | Allow direct reads of `.log` and transcript files. |
| `JUST_AGY_SMALL_LOG_BYTES=<int>` | Size limit for direct primary reads of a log or transcript that is a regular file at an absolute path (default 16384 = 16 KiB; `0` disables the exception). |
| `JUST_AGY_ALLOW_DIRECT_FASTPATH=1` | Disable the direct-edit cap and edit-then-test block. |
| `JUST_AGY_TURN_NUDGE_SECONDS=<sec>` | Seconds after a user request before the primary is nudged once to write the deliverable once work has been delegated during that request (default 300; `0` disables; invalid uses the default). |
| `JUST_AGY_CIRCUIT_BREAKER=0` | Disable the two-failure circuit breaker. |
| `JUST_AGY_DEBUG=1` | Append one JSON line per hook call to `just_agy_orchestrator_debug.log`. |
| `JUST_AGY_APPEND_REPORT_FORMAT=0` | Do not append the REPORT FORMAT block. |
| `JUST_AGY_ENFORCE_REPORT=0` | Do not validate subagent reports. |
| `JUST_AGY_ESCALATE=0` | Disable scout-to-worker escalation. |
| `JUST_AGY_WORKER_DOWNGRADE=0` | Disable the worker-lookup downgrade. |
| `JUST_AGY_JUDGE_URL` | Enable the optional judge (falls back to `AGY_ARGON_JUDGE_URL`, then `MITOSIS_JUDGE_URL`). |
| `JUST_AGY_JUDGE_KEY`, `_MODEL`, `_TIMEOUT`, `_MIN_CONFIDENCE`, `_THRESHOLD` | Judge credentials, model, timeout and confidence settings. |
| `JUST_AGY_JUDGE_ALLOW_HTTP_KEY=1` | Send the judge key over plain http (off by default). |
| `JUST_AGY_LOCK_TIMEOUT=<sec>` | State lock timeout, fails open (default 2). |
| `AGY_ANTIGRAVITY_SETTINGS` | Settings path override (for tests). |
| `JUST_AGY_GEMINI_CONFIG_DIR` | Override of `~/.gemini/config`, used by the mitosis coexistence check (tests). |

---

## Installation

```bash
git clone https://github.com/ivanusto/just-agy-orchestrator.git ~/.gemini/config/plugins/just-agy-orchestrator
# or, from any checkout:
agy plugin install <dir>
agy plugin validate . --strict
```

Restart `agy` afterwards. Toggle with `agy plugin enable just-agy-orchestrator` or `agy plugin disable just-agy-orchestrator`.

---

## Migrating from argon-orchestrator

This plugin was called `argon-orchestrator` up to 2.0.0. To migrate:

```bash
agy plugin uninstall argon-orchestrator
git clone https://github.com/ivanusto/just-agy-orchestrator.git ~/.gemini/config/plugins/just-agy-orchestrator
```

Restart `agy` afterwards. The old `AGY_ARGON_*` variables keep working, so no environment change is required. Like its predecessor, it still cannot be enabled together with `mitosis-orchestrator` (same skill and agent names).

---

## Relationship with mitosis-orchestrator

[mitosis-orchestrator](https://github.com/ivanusto/mitosis-orchestrator) is the successor that supports three platforms. Just agy Orchestrator is agy only and lighter. Both use the same skill and agent names, so **enable only one**: `SessionStart` warns when both are enabled. To switch to Just agy Orchestrator: `agy plugin disable mitosis-orchestrator`.

---

## Development

```bash
python3 -m unittest discover -s tests -v
python3 scripts/routing-parity.py <mitosis_repo>
ruff check .
agy plugin validate . --strict
```

See [CONTRIBUTING.md](./CONTRIBUTING.md), [SECURITY.md](./SECURITY.md) and [CHANGELOG.md](./CHANGELOG.md).

## License

[MIT](./LICENSE)
