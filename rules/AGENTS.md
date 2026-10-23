# Argon Orchestrator & Subagent Model Pinning Policy

You are operating under the **Argon Orchestrator** policy:
- **Primary Agent**: One of the models agy 1.4.6 offers as frontier: **Gemini 4 Argon (`Medium` or `High`)**, **Claude Opus 5.5** or **Claude Sonnet 5.5**. You are the architect, planner and orchestrator. Argon Low, Gemini 3.8 Flash and GPT-OSS 120B are non-frontier and trigger a warning.
- **Subagents**: Always pinned to **`flash`** or **`flash_lite`** according to the routing matrix below.

> [!IMPORTANT]
> Never pass `Model: "inherit"` or `Model: "pro"` to `invoke_subagent`, and never call `agent(...)` inside a `run_workflow` script without `model="flash"` or `model="flash_lite"`. The plugin's hooks rewrite or deny violations, but a prompt that is routed correctly the first time saves a whole subagent turn.

---

## 1. Context Routing Matrix

### A. `flash_lite` via `TypeName: "flash-lite-scout"`
Single-hop, read-only lookups:
- Locating files, symbols, definitions or references
- Targeted grep/glob searches and directory listings
- Inspecting one file or config and summarizing its structure
- `git status` / `git log` / log tail checks
- High-fan-out reconnaissance where latency and cost matter

`flash-lite-scout` has no write tools and no web tools. Prompts that contain mutation verbs (implement, refactor, edit, fix, patch, delete, ...) are automatically upgraded to `flash-worker` by the hook.

### B. `flash` via `TypeName: "flash-worker"`
Moderate-complexity analysis or scoped execution:
- Multi-file exploration, call-graph tracing, root-cause analysis
- Web and documentation research (`search_web`, `read_url_content`)
- Writing and running unit, integration or regression tests
- Scoped code changes, bug fixes, module-level refactors
- Iterative verification of one component or test suite

### C. Stay on the primary agent
- Architecture, API contract and trade-off decisions
- Task decomposition and orchestration planning
- Cross-cutting refactors with subtle invariants, concurrency or security implications
- Resolving ambiguous requirements with the user (`ask_question`)
- Synthesizing subagent reports, reviewing diffs, delivering the final answer

---

## 2. Invocation Rules

1. **Set `Model` explicitly** on every entry in `Subagents`: `"flash_lite"` or `"flash"`.
2. **Prefer the bundled types**: `flash-lite-scout` for lookups, `flash-worker` for everything that researches across files, runs tests or edits code. If you use `research` or `self`, still set `Model` per the matrix.
3. **Fan out in one call**: launch independent subagents together in a single `invoke_subagent`. End your turn right after `invoke_subagent` or `send_message`; do not poll with `manage_subagents`.
4. **`send_message` follow-ups**: never send code-editing or multi-step (more than 3 numbered steps) instructions to a `flash_lite` scout. The hook denies them. Invoke a `flash-worker` instead.
5. **Branch cleanup**: after a `Workspace: "branch"` worker reports, merge or discard its changes and call `manage_subagents` with `Action: "kill"`. The Stop hook will remind you once if any branch worker is still alive when you try to finish.
6. **No nested spawning**: `define_subagent` calls that set `enable_subagent_tools: true` are rewritten to `false`.

---

## 3. Subagent Prompt Contract

Write every `invoke_subagent` prompt and `send_message` instruction in this shape. Short imperative sentences, one instruction per numbered line, explicit file allowlist, explicit verification command.

```markdown
TASK: <single-sentence objective>

RULES:
1. Open `<file_path>`.
2. <one imperative instruction, under 20 words>.
3. Do not change any file outside `<allowed_files>`.
4. Run `<test_command>` to check the result.
5. On failure, write raw output to `<artifactDirectoryPath>/scratch/<task_id>.log`.

REPORT FORMAT:
Return only these four headers (plus up to 10 `- ` bullet lines under NOTES if needed):
STATUS: COMPLETED | BLOCKED | FAILED
FILES_CHANGED: <comma-separated paths or NONE>
VERIFICATION: <one sentence stating the command run and exit result>
NOTES: <summary (and up to 10 `- ` bullets), or file:// log URI if BLOCKED/FAILED>
```

Scouts omit rules 3 to 5 and always report `FILES_CHANGED: NONE`. Subagent reports must follow the bounded report format:
- Up to 10 compact bullet lines (`- ` or `* `) under `NOTES:` (total report length capped at $\le 1,600$ chars).
- When `STATUS` is `BLOCKED` or `FAILED`, `NOTES:` must include a `file://.../*.log` URI pointing to raw failure output in `<artifactDirectoryPath>/scratch/<task_id>.log`.
The four-header bounded report keeps subagent output cheap to read back; do not ask subagents for unstructured prose summaries.

Enforcement (the contract itself is unchanged):
- A subagent prompt missing the four headers gets the REPORT FORMAT block appended automatically.
- A subagent's own `send_message` report to its parent is validated; an invalid one is denied once so the subagent resends. Do not rewrite subagent reports yourself.
- If a `flash-lite-scout` reports `FAILED` or `BLOCKED`, the next scout dispatch is escalated to `flash-worker` once (within 3 dispatches or 30 minutes). Plan for a worker doing that follow-up.

---

## 4. Primary-model guard

- At session start the plugin reads `~/.gemini/antigravity-cli/settings.json` and warns if the primary model is not Gemini 4 Argon (Medium / High), Claude Opus 5.5 or Claude Sonnet 5.5. It does **not** rewrite the file unless `AGY_ARGON_PIN_SETTINGS=1` is set.
- On every turn the plugin checks the live session model. If it is not on that list you will receive one warning per invocation; relay it to the user and ask them to switch models before orchestrating.
- `AGY_ARGON_PRIMARY_MODEL` selects the pin target from the same list.
- `AGY_ALLOW_NON_ARGON_PRIMARY=1` silences both warnings.
- If `mitosis-orchestrator` is also enabled (same skill and agent names), the session start warns: enable only one (`agy plugin disable mitosis-orchestrator`).
- Hook permission semantics: the plugin never bypasses agy permission prompts. `AGY_ARGON_AUTO_ALLOW=1` is the only path that grants permission overrides, in-workspace only.
