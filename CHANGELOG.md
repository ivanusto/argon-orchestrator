# Changelog

## 2.0.0 — 2026-10-23

Antigravity (agy)-only release.

### Breaking
- Primary-model list narrowed to what agy 1.4.6 offers: Gemini 4 Argon (Medium / High), Claude Opus 5.5, Claude Sonnet 5.5. Argon Low, Gemini 3.8 Flash and GPT-OSS 120B are warned about as non-frontier.
- GPT-4 / o-series / DeepSeek / Grok and generic Gemini Pro / Ultra are no longer accepted as primary models, including via `AGY_ARGON_PRIMARY_MODEL`.

### Added
- agy 1.4.6 sends one flat `invoke_subagent` call per subagent (`Prompt`, `Role`, `TypeName`, optional `Model`); both the flat form and the `Subagents` list form are handled.
- Code split into `scripts/argon_hook/` (`common`, `models`, `routing`, `judge`, `report`, `state`, `guards`, `tools`, `handlers`); entry point stays `scripts/enforce-orchestrator.py`.
- Routing engine ported verbatim from mitosis-orchestrator 2.3.0: `Decision` with confidence, `scout-unproven` rule (scout needs positive read-only evidence), worker-lookup downgrade, Chinese rules, optional judge (`AGY_ARGON_JUDGE_URL`, fallback `MITOSIS_JUDGE_URL`).
- `tests/routing_cases.jsonl` (855) and `routing_holdout` v1 to v8 (40 each); `tests/test_routing_eval.py` thresholds: accuracy >= 0.95, cost errors <= 0.05, zero worker tasks to the scout. `scripts/routing-parity.py <mitosis_repo>` proves 0 differences.
- State lock (`flock`, fail open, `AGY_ARGON_LOCK_TIMEOUT`) and atomic state saves.
- REPORT FORMAT block auto-appended to subagent prompts missing the four headers (`AGY_ARGON_APPEND_REPORT_FORMAT=0` disables).
- Report enforcement: a subagent's own `send_message` report to its parent is validated; invalid reports are denied once so the subagent resends (`AGY_ARGON_ENFORCE_REPORT=0`).
- Scout read-only layer for `flash-lite-scout` subagents.
- Scout escalation: after a scout reports `FAILED` or `BLOCKED`, the next scout dispatch is escalated to `flash-worker` once, within 3 dispatches or 30 minutes (`AGY_ARGON_ESCALATE=0`). First agy implementation.
- `AGY_ARGON_WORKER_DOWNGRADE=0` and judge tuning variables (`AGY_ARGON_JUDGE_{KEY,MODEL,TIMEOUT,MIN_CONFIDENCE,ALLOW_HTTP_KEY,THRESHOLD}`).
- Coexistence: `SessionStart` warns when mitosis-orchestrator is also enabled (same skill and agent names; use `agy plugin disable mitosis-orchestrator`).
- Engineering scaffolding: `pyproject.toml`, GitHub Actions CI (unittest on 3.11 to 3.13, ruff, vermin 3.8), `tests/test_packaging.py`, `CONTRIBUTING.md`, `SECURITY.md`.

### Changed
- README, README.zh-TW, `rules/AGENTS.md` and the `/orchestrate` skill rewritten for agy only; agent files no longer imply an Argon-only primary.
- The four-header report contract is unchanged.
- Routing keyword rules read the `Prompt` only; `Role` is a label and no longer feeds them (a lookup scout with Role "Add Scout" was wrongly upgraded to `flash-worker`). Tests added.
- Documented that the bundled agents keep `inheritCustomizations: false`, so subagent-side report rejection and the scout read-only layer only fire for types that inherit customizations; primary-side message scan and scout-failure escalation cover the bundled types.

### Verified
- agy 1.4.6 (2026-10-23): agy does not run plugin hooks inside subagents with `inheritCustomizations: false`, and enforces the agent tool lists itself (a scout given an edit task could not edit). Routing parity with mitosis stays at 0 differences (parity cases carry no role).
- agy 1.4.6 (2026-10-23): hook `{}` is a deny; `{"decision":"allow"}` means no objection and agy still shows its own permission prompt for commands and file writes. The plugin keeps returning explicit allow when it has no opinion and never bypasses agy prompts; `AGY_ARGON_AUTO_ALLOW=1` is the only path that grants `permissionOverrides` (in-workspace only).

## 1.4.0 — 2026-10-19

### Added
- **Frontier Model Support for Primary Orchestrator**: Expanded `is_argon_tier` (aliased as `is_frontier_tier`) to support Claude (Opus / Sonnet), Gemini Pro / Ultra, OpenAI frontier/reasoning models (GPT-4, GPT-5, o1, o3, o4), DeepSeek, and Grok alongside Gemini 4 Argon (`High` / `Medium`).
- **Small-Tier Rejection Guard**: Word-boundary regex filtering (`\b(flash|lite|mini|nano|haiku)\b`) rejects small-tier models (such as Claude Haiku, GPT-4o mini, o3-mini) from being accidentally treated as primary orchestrators while correctly distinguishing `gemini`.
- **Flexible Primary Model Pinning**: `check_primary_model_settings()` now honors any valid Argon or frontier model specified via `AGY_ARGON_PRIMARY_MODEL` (e.g., `Claude 3.7 Sonnet`) when `AGY_ARGON_PIN_SETTINGS=1` is opted in.

## 1.3.0 — 2026-10-19

### Fixed
- **`PreToolUse` hook no longer returns empty `{}` (which Antigravity treated as an unapproved tool denial).** When `AGY_ARGON_AUTO_ALLOW` is unset, when a tool has no path arguments (`read_url_content`, `search_web`, browser/MCP tools), or on fallback/error, it now returns an explicit pass-through `{"decision": "allow", "reason": "Argon Orchestrator: pass-through."}`.

### Added
- **Context Hygiene Guard**: Blocks direct inspection of `.log` or `transcript.jsonl` / `transcript_full.jsonl` files by the primary agent in `PreToolUse` (unless `AGY_ARGON_ALLOW_DIRECT_LOGS=1`), delegating log slicing to `flash-lite-scout`.
- **Fast-Path Direct Execution Budget**: Tracks direct workspace file mutations in `turn_edited_files`, capping the primary agent at `AGY_ARGON_MAX_DIRECT_EDITS` (default 3 distinct workspace files in one turn) and blocking direct workspace edit followed by test runner execution in the same turn (unless `AGY_ARGON_ALLOW_DIRECT_FASTPATH=1`), delegating code editing and test execution loops to `flash-worker`.
- **Two-Failure Circuit Breaker**: Tracks consecutive failures across `run_command` and `invoke_subagent` in `PostToolUse` (matcher updated to `invoke_subagent|manage_subagents|run_command`). Trips after 2 consecutive failures and denies further commands or subagent invocations in `PreToolUse` (unless `AGY_ARGON_CIRCUIT_BREAKER=0`).
- **Per-Turn Tracking Reset**: `PreInvocation` automatically resets `turn_edited_files` and `consecutive_failures` on each turn.
- **Bounded 4-Header Subagent Report Contract**: Expanded the subagent report format across rules and agents to support up to 10 compact bullet lines (`- ` or `* `) under `NOTES:` (total length $\le 1,600$ characters) and mandatory `file://` scratch log URI on `BLOCKED` or `FAILED`. Added `validate_subagent_report(text)` validation helper in `enforce-orchestrator.py`.

## 1.2.0 — 2026-10-16

### Fixed
- **Classifier misrouted heavy work to `flash_lite`.** A single lite keyword such as `find` could override strong signals (`test`, `trace`, `call graph`, `refactor`). Strong signals now always win, and mutation prompts never stay on `flash_lite` regardless of `Workspace`.
- **Over-broad `flash_lite` vocabulary.** Bare words `find`, `ls`, `rg`, `count`, `version`, `format`, `status`, `check if` were removed; only concrete lookup phrases remain.

### Changed
- **`SessionStart` no longer rewrites `settings.json` by default.** It warns when the primary model is not Argon. Set `AGY_ARGON_PIN_SETTINGS=1` to restore the 1.1.x rewrite behaviour.
- `flash-lite-scout` lost `search_web`, `read_url_content` and `schedule`; web research is `flash-worker` work per the routing matrix. Both agents now return a fixed four-line report (`STATUS / FILES_CHANGED / VERIFICATION / NOTES`).
- `rules/AGENTS.md` gained the subagent prompt contract, `send_message` rules and branch-cleanup rules.

### Added
- **`PreInvocation` hook**: checks the live session model every turn and warns once per invocation if it is not Argon/Pro-tier. Catches model switches made in the UI after session start.
- **`send_message` guard**: denies mutation or multi-step (>3 numbered rules) instructions sent to a known `flash_lite` recipient.
- **`run_workflow` guard**: denies scripts whose `agent(...)` calls omit `model="flash"` / `model="flash_lite"`.
- **`define_subagent` guard**: forces `enable_subagent_tools=false` (no nested spawning).
- **Branch worktree lifecycle tracking** via `PostToolUse` + `Stop`: pending spawns are paired with the per-subagent metadata agy writes under `<artifactDirectoryPath>/.system_generated/subagents/` (type, role, live state, worktree URI), with the transcript as fallback; the `Stop` hook asks once per set of live `Workspace:"branch"` workers to merge/discard and kill them (agy bumps `executionNum` after every `continue`, so per-execution dedup would nag on every attempt).
- A `flash-lite-scout` invoked with a mutation prompt is upgraded to `flash-worker` instead of failing for lack of write tools.
- **Workspace scope guard (opt-in, `AGY_ARGON_AUTO_ALLOW=1`)**: the `PreToolUse` matcher is now `*`. `run_command` and file tools that stay inside `workspacePaths` / `artifactDirectoryPath` are answered `allow` (with a `permissionOverrides` entry for the exact command); paths outside the workspace, privilege escalation, recursive deletes of a root, system-altering commands, force pushes and downloads piped to a shell are answered `ask`; tools without path arguments get no opinion. Unknown tools and hook errors now return `{}` instead of `allow`. File tools carry a matching `read_file(<path>)` / `write_file(<path>)` override. Interactive sessions only (verified on agy 1.4.0: no prompts for in-workspace edits and commands); headless `agy -p` soft-denies prompt-requiring tools regardless of hook output.
- `AGY_ARGON_DEBUG=1` writes one JSON line per hook call for diagnosing payloads.
- `tests/test_enforce_orchestrator.py`: 27 unit tests, including a table of prompt → tier cases (the first three rows are the 1.1.0 misroutes).

## 1.1.0

- Support both Gemini 4 Argon (High) and (Medium) automatically as primary orchestrator.

## 1.0.0

- Initial release: Argon primary + flash/flash_lite subagents.
