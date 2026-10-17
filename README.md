# Argon Orchestrator (`argon-orchestrator`)

[繁體中文說明 (README.zh-TW.md)](./README.zh-TW.md)

An Antigravity (`agy`) plugin that pins **Gemini 4 Argon (`High` or `Medium`)** as the primary orchestrator agent (automatically preserving whichever thinking tier you select in the UI/CLI) and enforces context-aware model pinning (`flash` or `flash_lite`) for all subagents.

---

## Architecture & Context Routing Policy

| Tier | Model | Role & Responsibilities |
| :--- | :--- | :--- |
| **Primary Orchestrator** | `Gemini 4 Argon (High / Medium)` | High-level architecture, multi-step planning, cross-cutting refactors, complex reasoning, security review, and final synthesis. Automatically follows your selected `High` or `Medium` thinking tier. |
| **Tier 1 Subagent (`flash-worker`)** | `flash` | Multi-file codebase research, web/documentation synthesis, writing & running tests, and scoped code edits or debugging. |
| **Tier 2 Subagent (`flash-lite-scout`)** | `flash_lite` | Fast file/symbol lookups, grep/glob searches, git/log status checks, single-file inspection, and lightweight summarization. |

---

## Bundled Components

- **[`rules/AGENTS.md`](./rules/AGENTS.md)**: Always-on orchestration policy: the routing matrix, invocation rules, the four-line subagent report contract, and the branch-cleanup rule.
- **[`hooks.json`](./hooks.json) & [`scripts/enforce-orchestrator.py`](./scripts/enforce-orchestrator.py)** (Python 3.8+, standard library only):

  | Hook | What it does |
  | :--- | :--- |
  | `SessionStart` | Reads `~/.gemini/antigravity-cli/settings.json`, **warns** if the primary model is not Argon, and injects the policy notice. Rewrites the file only when `AGY_ARGON_PIN_SETTINGS=1`. |
  | `PreInvocation` | Checks the live session model every turn; warns once per invocation if it is not Argon/Pro-tier (catches UI model switches). |
  | `PreToolUse` `invoke_subagent` | Classifies every subagent into `flash` / `flash_lite` from `TypeName`, `Workspace`, `Role` and `Prompt`. Strong signals (tests, debugging, tracing, mutation verbs) always beat lookup keywords. A `flash-lite-scout` given an edit task is upgraded to `flash-worker`. |
  | `PreToolUse` `send_message` | Denies code-change or multi-step (>3 numbered rules) follow-ups sent to a `flash_lite` recipient. |
  | `PreToolUse` `run_workflow` | Denies scripts whose `agent(...)` calls lack `model="flash"` / `model="flash_lite"`. |
  | `PreToolUse` `define_subagent` | Forces `enable_subagent_tools=false`. |
  | `PreToolUse` other tools (opt-in, `AGY_ARGON_AUTO_ALLOW=1`) | Workspace scope guard: `run_command` and file tools whose cwd, paths and command text stay inside `workspacePaths` / `artifactDirectoryPath` are answered `allow`; paths outside, privilege escalation, recursive deletes of a root, system-altering commands, force pushes and downloads piped to a shell are answered `ask`; tools without path arguments (browser, MCP, web) get no opinion. |
  | `PostToolUse` | Pairs spawned subagents with the metadata agy writes under `<artifactDirectoryPath>/.system_generated/subagents/` (type, role, live state, worktree); rolls back on failed spawns. |
  | `Stop` | If `Workspace:"branch"` workers are still alive when the turn is fully idle, asks once per set of live workers to merge/discard and kill them. Never blocks cancellations, errors or budget caps. |

- **[`agents/`](./agents/)**:
  - **[`flash-lite-scout`](./agents/flash-lite-scout/agent.md)**: read-only scout pinned to `model: flash_lite`; local lookup tools only, no web, no writes.
  - **[`flash-worker`](./agents/flash-worker/agent.md)**: research & execution worker pinned to `model: flash`; edits only within the prompt's file allowlist and stops after two failed attempts.
  - Both return a fixed four-line report (`STATUS / FILES_CHANGED / VERIFICATION / NOTES`) so the orchestrator reads back minimal tokens.
- **[`skills/orchestrate/SKILL.md`](./skills/orchestrate/SKILL.md)**: the `/orchestrate` four-phase workflow (`flash_lite` recon → Argon plan → `flash` execution → Argon verification and branch cleanup).

### Environment variables

| Variable | Effect |
| :--- | :--- |
| `AGY_ARGON_PIN_SETTINGS=1` | Opt in to rewriting `settings.json` back to an Argon model at session start (1.1.x behaviour). |
| `AGY_ARGON_PRIMARY_MODEL` | Target model when pinning: `Gemini 4 Argon (High)` (default) or `Gemini 4 Argon (Medium)`. |
| `AGY_ALLOW_NON_ARGON_PRIMARY=1` | Silence all primary-model warnings. |
| `AGY_ARGON_AUTO_ALLOW=1` | Enable the workspace scope guard (off by default). |
| `AGY_ARGON_DEBUG=1` | Append one JSON line per hook call to `<artifactDirectoryPath>/argon_orchestrator_debug.log` (mode, tool, payload keys, decision). |

---

## Installation

### Option 1: Clone directly into Antigravity global plugins directory

```bash
git clone https://github.com/ivanusto/argon-orchestrator.git ~/.gemini/config/plugins/argon-orchestrator
```

### Option 2: Install from a local checkout via `agy plugin`

```bash
git clone https://github.com/ivanusto/argon-orchestrator.git ~/argon-orchestrator
agy plugin validate ~/argon-orchestrator
agy plugin install ~/argon-orchestrator
```

Restart `agy` after installing. You can manage or toggle the plugin at any time with `/plugin` or `agy plugin enable argon-orchestrator` / `agy plugin disable argon-orchestrator`.

---

## Development

```bash
python3 -m unittest discover -s tests -v
agy plugin validate . --strict
```

**Headless note.** In `agy -p` (print) mode Antigravity soft-denies every tool that would need a permission prompt, regardless of hook decisions or `permissionOverrides`; only `settings.json` allow-rules or `--dangerously-skip-permissions` apply there. The workspace scope guard therefore only removes prompts in interactive sessions (verified interactively on agy 1.4.0: in-workspace edits and test commands ran without a prompt).

Verified live on `agy` 1.4.0 (2026-10-16): `SessionStart` and `PreInvocation` notices reach the model; the live model id arrives as e.g. `gemini-4-argon-high` / `gemini-3.8-flash-high`; `Stop` receives `fullyIdle`, `executionNum` and `terminationReason` (`NO_TOOL_CALL` on a normal end of turn); subagent metadata files flip from `SUBAGENT_STATE_ALIVE` to `SUBAGENT_STATE_KILLED` after `manage_subagents(Action="kill")`.

See [CHANGELOG.md](./CHANGELOG.md) for release notes.

---

## License

[MIT](./LICENSE)
