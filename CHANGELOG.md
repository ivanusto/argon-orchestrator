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
- **Branch worktree lifecycle tracking** via `PostToolUse` + `Stop`: pending spawns are paired with the per-subagent metadata agy writes under `<artifactDirectoryPath>/.system_generated/subagents/` (type, role, live state, worktree URI), with the transcript as fallback; the `Stop` hook asks once per set of live `Workspace:"branch"` workers to merge/discard and kill them (agy bumps `executionNum` after every `continue`, so per-execution dedup would nag on every attempt).
- A `flash-lite-scout` invoked with a mutation prompt is upgraded to `flash-worker` instead of failing for lack of write tools.
- **Workspace scope guard (opt-in, `AGY_ARGON_AUTO_ALLOW=1`)**: the `PreToolUse` matcher is now `*`. `run_command` and file tools that stay inside `workspacePaths` / `artifactDirectoryPath` are answered `allow` (with a `permissionOverrides` entry for the exact command); paths outside the workspace, privilege escalation, recursive deletes of a root, system-altering commands, force pushes and downloads piped to a shell are answered `ask`; tools without path arguments get no opinion. Unknown tools and hook errors now return `{}` instead of `allow`. File tools carry a matching `read_file(<path>)` / `write_file(<path>)` override. Interactive sessions only (verified on agy 1.4.0: no prompts for in-workspace edits and commands); headless `agy -p` soft-denies prompt-requiring tools regardless of hook output.
- `AGY_ARGON_DEBUG=1` writes one JSON line per hook call for diagnosing payloads.
- `tests/test_enforce_orchestrator.py`: 27 unit tests, including a table of prompt → tier cases (the first three rows are the 1.1.0 misroutes).

## 1.1.0

- Support both Gemini 4 Argon (High) and (Medium) automatically as primary orchestrator.

## 1.0.0

- Initial release: Argon primary + flash/flash_lite subagents.
