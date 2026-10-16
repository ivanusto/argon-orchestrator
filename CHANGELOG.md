# Changelog

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
- **Branch worktree lifecycle tracking** via `PostToolUse` + `Stop`: pending spawns are paired with subagent `conversationId`s from the transcript; the `Stop` hook asks once per execution to merge/discard and kill live `Workspace:"branch"` workers.
- A `flash-lite-scout` invoked with a mutation prompt is upgraded to `flash-worker` instead of failing for lack of write tools.
- `tests/test_enforce_orchestrator.py`: 23 unit tests, including a table of prompt → tier cases (the first three rows are the 1.1.0 misroutes).

## 1.1.0

- Support both Gemini 4 Argon (High) and (Medium) automatically as primary orchestrator.

## 1.0.0

- Initial release: Argon primary + flash/flash_lite subagents.
