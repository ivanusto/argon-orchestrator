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

- **[`rules/AGENTS.md`](./rules/AGENTS.md)**: Always-on orchestration policy instructing the primary Argon agent to decompose work and explicitly pin subagent invocations (`invoke_subagent` and `run_workflow`) to `flash` or `flash_lite` depending on context (never `inherit` or `pro`).
- **[`hooks.json`](./hooks.json) & [`scripts/enforce-orchestrator.py`](./scripts/enforce-orchestrator.py)**:
  - **`SessionStart` Hook**: Verifies that `~/.gemini/antigravity-cli/settings.json` uses `"Gemini 4 Argon (High)"` or `"Gemini 4 Argon (Medium)"` as the primary agent (automatically preserving whichever tier you selected in the UI/CLI, or restoring `"Gemini 4 Argon (High)"` if set to a non-Argon model; configurable via `AGY_ARGON_PRIMARY_MODEL` or `AGY_ALLOW_NON_ARGON_PRIMARY=1`) and injects an active orchestration policy notice.
  - **`PreToolUse` Hook (`invoke_subagent`)**: Intercepts subagent invocations and rewrites any `"inherit"`, `"pro"`, or omitted `Model` fields to `"flash"` or `"flash_lite"` using context classification over `TypeName`, `Workspace`, `Role`, and `Prompt`.
- **[`agents/`](./agents/)**:
  - **[`flash-lite-scout`](./agents/flash-lite-scout/agent.md)**: Pre-configured read-only scout subagent pinned to `model: flash_lite`.
  - **[`flash-worker`](./agents/flash-worker/agent.md)**: Pre-configured research & execution subagent pinned to `model: flash`.
- **[`skills/orchestrate/SKILL.md`](./skills/orchestrate/SKILL.md)**: Provides the `/orchestrate` workflow for structured 4-phase orchestration (`flash_lite` Recon $\rightarrow$ Argon Plan $\rightarrow$ `flash` Execution $\rightarrow$ Argon Verification).

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
