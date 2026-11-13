# Changelog

## 3.0.1 - 2026-11-13

### Added
- Gemini 4 Hydrogen (agy 1.5.7; ids `gemini-4-hydrogen-low|medium|high|xhigh`, plain `gemini-4-hydrogen` is High) is a frontier primary at High and XHigh (also "Extra High"); Low and Medium are warned about.
- agy Orchestrator (`gemini-pro-orchestrator` = "Orchestrator (Carbon)", `gemini-pro-orchestrator-argon` = "Orchestrator (Argon)") is a frontier primary. It plans with Carbon/Argon and executes with Hydrogen internally, so the plugin's dispatch stacks on top of it.
- `Gemini 4 Hydrogen (High)`, `(XHigh)`, `Orchestrator (Carbon)` and `Orchestrator (Argon)` are valid `JUST_AGY_PRIMARY_MODEL` pin targets. The default stays `Gemini 4 Argon (High)`.
- agy Orchestrator (Carbon) hands execution to an internal executor that hook payloads report as `gemini-flash-worker-agy`; it is accepted and no longer triggers the non-frontier warning.
- Verified on agy 1.5.7 with headless `/orchestrate` runs (scout read plus worker edit) using `gemini-4-hydrogen-high` and `gemini-pro-orchestrator` as primary, in an isolated home with only this plugin installed: subagents pinned to `flash_lite` / `flash`, reports passed the four-header check, and no primary-model warning was injected (including the executor phase reported as `gemini-flash-worker-agy`). The concrete model behind the `flash` tier is resolved server side and is not visible to the CLI or hooks.

## 3.0.0 - 2026-11-11

### Breaking: renamed to just-agy-orchestrator
- Plugin name is now `just-agy-orchestrator` (display name "Just agy Orchestrator"); it was `argon-orchestrator`. User-visible hook messages read `[Just agy Orchestrator]`.
- `hooks.json` top-level key is now `just-agy-orchestrator`.
- Package renamed from `scripts/argon_hook/` to `scripts/just_agy_hook/`. State, lock and debug files are renamed too (`just_agy_orchestrator_state.json`, `.just_agy_orchestrator_state.lock`, `just_agy_orchestrator_debug.log`); old files are not read.
- Environment prefix is now `JUST_AGY_*` (`AGY_ARGON_<X>` becomes `JUST_AGY_<X>`; `AGY_ALLOW_NON_ARGON_PRIMARY` and `AGY_ARGON_ALLOW_NON_PRIMARY` become `JUST_AGY_ALLOW_NON_PRIMARY`). The legacy names still work as a fallback, and `JUST_AGY_*` wins when both are set. Judge settings fall back `JUST_AGY_JUDGE_*`, `AGY_ARGON_JUDGE_*`, `MITOSIS_JUDGE_*`.
- `is_frontier_tier` is the primary name; `is_argon_tier` stays as an alias.
- Migration: `agy plugin uninstall argon-orchestrator`, then install `just-agy-orchestrator` (https://github.com/ivanusto/just-agy-orchestrator). Environment variables need no change. It still cannot be enabled together with `mitosis-orchestrator` (same skill and agent names).

### Fixed
- Gemini 4 Carbon (agy 1.5.5, ADC sign-in only; ids `gemini-4-carbon-low|medium|high`) is a frontier primary on the same rule as Argon: Medium and High accepted, Low warned about, display and id forms both recognised. `Gemini 4 Carbon (High)` and `(Medium)` are valid `JUST_AGY_PRIMARY_MODEL` pin targets. The default stays `Gemini 4 Argon (High)`.
- agy 1.5 toolset (verified on agy 1.5.5): `find_by_name`, `grep_search` and `list_dir` are retired, so a scout has only `read_file`, `send_message` and `manage_task`. The scout instructions and the session policy notice now say scouts only read paths you name and that searches, git/log checks, test runs, web research and edits go to `flash-worker`. The agent tool lists keep the old names, which agy resolves as aliases of `read_file`, `edit_file` and `write_file`; listing an old name next to its new name fails the agent build.
- Windows: agy runs hooks via `cmd /c`, where the Store alias `python3.exe` exits 9009 with no output and agy treats a failed PreToolUse hook as a tool error, so every tool call failed. Each hook command now tries `python3`, then `python`, then `py -3`, and ends in `exit 0` (never `echo {}`, which PreToolUse reads as a denial). The hook also reads stdin as UTF-8 bytes and strips a BOM.
- agy 1.5 edit budget: `write_file` and `edit_file` (arg `Path`) are now counted, and `turn_edited_files` resets only when `invocationNum` is 0 (a new user turn) instead of on every model call. The circuit breaker is unchanged. The budget never counted on agy 1.5 before; it now denies the 4th distinct file per turn (default 3, `JUST_AGY_MAX_DIRECT_EDITS`), and `JUST_AGY_ALLOW_DIRECT_FASTPATH=1` turns it off.
- Report check: a bare `NOTES:` line followed by `- ` bullets is now accepted, as in mitosis-orchestrator. In a live agy 1.5.5 run two of three scouts wrote their findings that way and were flagged as breaking the report contract.

### Added
- `tests/test_agy_toolset.py`, `tests/test_hook_launcher.py` (runs the real `hooks.json` commands through `cmd.exe` or `/bin/sh` with a PATH holding selected interpreters, plus a stdin encoding test), a packaging test for the launcher chain, and Carbon and `Path`-budget tests.
- `hooks-windows` CI job on `windows-latest` running the launcher and packaging tests.

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
