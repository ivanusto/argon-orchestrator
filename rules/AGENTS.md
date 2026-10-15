# Argon Orchestrator & Subagent Model Pinning Policy

You are operating under the **Argon Orchestrator** policy:
- **Primary Agent**: Pinned to **Gemini 4 Argon (`High` or `Medium`)** (automatically following your selected Argon thinking tier). You serve as the lead architect, planner, and orchestrator.
- **Subagents**: Must **always** be pinned to either **`flash`** or **`flash_lite`** depending on task context.

> [!IMPORTANT]
> **Override of Default `inherit` Instruction**:
> The user has explicitly requested that subagents NEVER use `Model: "inherit"` or `Model: "pro"`. Every `invoke_subagent` call (and every `agent()` call inside `run_workflow`) MUST explicitly set `Model` to either `"flash_lite"` or `"flash"` according to the Context Routing Matrix below.

---

## 1. Context Routing Matrix (`flash_lite` vs. `flash` vs. Primary Argon)

### A. Pin to `flash_lite` (`Model: "flash_lite"`)
Use **`flash_lite`** (via custom subagent `flash-lite-scout` or built-in `research` with `Model: "flash_lite"`) when the delegated context involves **fast, bounded, low-complexity lookups**:
- Locating files, symbols, definitions, or references across the workspace
- Running targeted regex/grep searches or directory listings
- Inspecting a single file or config and summarizing its structure
- Checking logs, git history, or task/service status
- Formatting, schema validation, or simple extraction tasks
- High-fan-out reconnaissance where latency and token cost matter most

### B. Pin to `flash` (`Model: "flash"`)
Use **`flash`** (via custom subagent `flash-worker`, built-in `research`, or `self` with `Model: "flash"`) when the delegated context involves **moderate-complexity analysis or scoped execution**:
- Multi-file codebase exploration, call-graph tracing, or root-cause analysis
- External web research and documentation synthesis (`search_web`, `read_url_content`)
- Writing and running unit, integration, or regression tests
- Implementing well-scoped code changes, bug fixes, or module-level refactors in an isolated or shared workspace
- Iterative verification of a single component or test suite

### C. Keep on Primary Agent (`Gemini 4 Argon (High / Medium)`)
Do **not** delegate the following to subagents; execute them directly on the primary Argon agent:
- System architecture design, API contract design, and trade-off analysis
- Multi-step task decomposition and orchestration planning
- Complex, cross-cutting refactors with subtle invariants or concurrency/security implications
- Resolving ambiguous user requirements (`ask_question`)
- Synthesizing subagent findings, reviewing diffs, and delivering the final verified response

---

## 2. Subagent Invocation Rules

1. **Always Specify `Model` Explicitly**:
   - When calling `invoke_subagent`, always pass `"Model": "flash_lite"` or `"Model": "flash"` on every entry in `Subagents`.
   - Never pass `"Model": "inherit"` or `"Model": "pro"`.
2. **Prefer Purpose-Built Subagent Types**:
   - Use `TypeName: "flash-lite-scout"` (with `Model: "flash_lite"`) for read-only lookups and fast reconnaissance.
   - Use `TypeName: "flash-worker"` (with `Model: "flash"`) for multi-file research, test execution, and scoped file edits.
   - If using `TypeName: "research"` or `TypeName: "self"`, still explicitly set `Model: "flash_lite"` or `Model: "flash"` based on the routing matrix above.
3. **Parallel Fan-Out & Context Hygiene**:
   - Launch independent `flash_lite` scouts or `flash` workers concurrently in a single `invoke_subagent` call.
   - Give each subagent a self-contained `Prompt` with exact file paths, constraints, and expected return format so it does not waste turns rediscovering context.
4. **Dynamic Workflows (`run_workflow`)**:
   - When writing a Python workflow script for `run_workflow`, always pass `model="flash_lite"` or `model="flash"` to every `agent(...)` call.
