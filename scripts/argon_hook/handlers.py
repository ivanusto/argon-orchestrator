"""Hook mode handlers."""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from .common import (
    LOG_OR_TRANSCRIPT_RE,
    MUTATION_TOOLS,
    READ_TOOLS,
    SAFE_TERMINATION_REASONS,
    TEST_RUNNER_RE,
    _allow,
    _deny,
    _env_flag,
    _get,
    is_primary_agent,
)
from .guards import _handle_workspace_guard, _within_roots
from .models import POLICY_NOTICE, check_primary_model_settings, is_argon_tier
from .state import _state_file, load_state, save_state, sync_subagents
from .tools import (
    _handle_define_subagent,
    _handle_invoke_subagent,
    _handle_manage_subagents,
    _handle_run_workflow,
    _handle_send_message,
)


def handle_session_start(_payload: Dict[str, Any]) -> Dict[str, Any]:
    status, model = check_primary_model_settings()
    if status == "ok":
        note = f"Primary model is '{model}'."
    elif status == "pinned":
        note = f"Restored primary model pin to '{model}' in settings.json (AGY_ARGON_PIN_SETTINGS=1)."
    elif status == "warn":
        note = (
            f"WARNING: settings.json primary model is '{model or 'unset'}', not an Argon model. "
            "Ask the user to switch to Gemini 4 Argon (High or Medium) before orchestrating, "
            "or set AGY_ALLOW_NON_ARGON_PRIMARY=1 to silence this."
        )
    elif status == "silenced":
        note = f"Primary model is '{model}' (non-Argon warnings silenced)."
    else:
        note = "settings.json not found; the live model is checked on every turn instead."
    return {
        "injectSteps": [
            {"systemMessage": {"systemMessage": f"[Argon Orchestrator Active] {note} {POLICY_NOTICE}"}}
        ]
    }


# ---------------------------------------------------------------------------
# pre-invocation
# ---------------------------------------------------------------------------

def handle_pre_invocation(payload: Dict[str, Any]) -> Dict[str, Any]:
    state = load_state(payload)
    state["turn_edited_files"] = []
    state["consecutive_failures"] = 0
    save_state(payload, state)

    model_name = str(payload.get("modelName") or "")
    if not model_name or is_argon_tier(model_name) or _env_flag("AGY_ALLOW_NON_ARGON_PRIMARY"):
        return {}
    key = f"{payload.get('conversationId', '')}:{payload.get('invocationNum', 0)}"
    warned: List[str] = state.setdefault("warned_invocations", [])
    if key in warned:
        return {}
    warned.append(key)
    save_state(payload, state)
    return {
        "injectSteps": [
            {
                "ephemeralMessage": (
                    f"[Argon Orchestrator] The active session model is '{model_name}', which is "
                    "not an Argon/Pro-tier model. Tell the user to switch Model Selection to "
                    "Gemini 4 Argon (High or Medium) before continuing orchestration work."
                )
            }
        ]
    }


def handle_pre_tool_use(payload: Dict[str, Any]) -> Dict[str, Any]:
    tool_call = payload.get("toolCall")
    if not isinstance(tool_call, dict):
        return _allow("Argon Orchestrator: pass-through.")
    tool_name = str(tool_call.get("name") or "").strip()
    args = tool_call.get("args")
    if not isinstance(args, dict):
        return _allow("Argon Orchestrator: pass-through.")

    state = load_state(payload)

    # Circuit breaker: if consecutive failures >= 2 and breaker is not disabled ("0"),
    # block retry loops on commands and subagents.
    if os.environ.get("AGY_ARGON_CIRCUIT_BREAKER") != "0":
        fails = int(state.get("consecutive_failures") or 0)
        if fails >= 2 and tool_name in ("run_command", "invoke_subagent"):
            return _deny(
                f"[Argon Orchestrator] Circuit breaker tripped after {fails} consecutive failure(s). "
                "Stop retrying and report the blocker to the user."
            )

    if tool_name == "invoke_subagent":
        return _handle_invoke_subagent(payload, args)
    if tool_name == "send_message":
        return _handle_send_message(payload, args)
    if tool_name == "run_workflow":
        return _handle_run_workflow(args)
    if tool_name == "define_subagent":
        return _handle_define_subagent(args)
    if tool_name == "manage_subagents":
        return _handle_manage_subagents(payload, args)

    # Primary agent guards (context hygiene and fast-path budget)
    if is_primary_agent(payload, state):
        # Context hygiene: block direct reads of raw logs or transcripts by primary agent
        if not _env_flag("AGY_ARGON_ALLOW_DIRECT_LOGS") and tool_name in READ_TOOLS:
            target = str(
                args.get("AbsolutePath")
                or args.get("TargetFile")
                or args.get("FilePath")
                or args.get("Path")
                or args.get("File")
                or ""
            )
            if target and LOG_OR_TRANSCRIPT_RE.search(target.replace("\\", "/")):
                return _deny(
                    f"[Argon Orchestrator] Direct inspection of log/transcript file '{Path(target).name}' "
                    "by the primary agent is blocked for context hygiene. "
                    "Delegate log slicing to `flash-lite-scout` (TypeName='flash-lite-scout')."
                )

        # Fast-path budget: direct workspace file mutations and test-runner loops
        if not _env_flag("AGY_ARGON_ALLOW_DIRECT_FASTPATH"):
            if tool_name in MUTATION_TOOLS:
                target = str(args.get("TargetFile") or args.get("AbsolutePath") or args.get("FilePath") or "")
                artifact_dir = payload.get("artifactDirectoryPath")
                is_artifact = False
                if artifact_dir and target:
                    try:
                        is_artifact = _within_roots(target, [Path(artifact_dir)])
                    except Exception:
                        is_artifact = False
                if target and not is_artifact:
                    try:
                        norm_target = str(Path(os.path.expanduser(target)).resolve())
                    except Exception:
                        norm_target = target
                    edited: List[str] = state.setdefault("turn_edited_files", [])
                    max_edits = int(os.environ.get("AGY_ARGON_MAX_DIRECT_EDITS") or 3)
                    if norm_target not in edited and len(edited) >= max_edits:
                        return _deny(
                            f"[Argon Orchestrator] Direct workspace edit limit ({max_edits} files per turn) exceeded "
                            f"when editing '{Path(target).name}'. Delegate multi-file modifications to `flash-worker` "
                            "(TypeName='flash-worker', Model='flash')."
                        )
                    if norm_target not in edited:
                        edited.append(norm_target)
                        save_state(payload, state)

            elif tool_name == "run_command":
                cmd = str(_get(args, "CommandLine", "commandLine", "command", default="") or "").strip()
                edited = state.get("turn_edited_files", [])
                if edited and TEST_RUNNER_RE.search(cmd):
                    return _deny(
                        "[Argon Orchestrator] Direct workspace edit followed by test runner execution detected "
                        f"in the primary agent ('{cmd}'). Delegate code editing and test execution loops "
                        "to `flash-worker` (TypeName='flash-worker', Model='flash')."
                    )

    return _handle_workspace_guard(payload, tool_name, args)


def _tool_failed(tool_name: str, payload: Dict[str, Any]) -> bool:
    if payload.get("error"):
        return True
    tool_result = payload.get("toolResult") or payload.get("result")
    if isinstance(tool_result, dict):
        exit_code = tool_result.get("exitCode")
        if exit_code is None:
            exit_code = tool_result.get("exit_code")
        if exit_code is None:
            exit_code = tool_result.get("returncode")
        if exit_code is not None and exit_code != 0:
            return True
        if tool_result.get("error"):
            return True
        output = str(tool_result.get("output") or "")
        if re.search(r"\bexited with code [1-9]", output):
            return True
        if tool_name == "invoke_subagent":
            res_str = json.dumps(tool_result)
            if re.search(r"STATUS:\s*(?:FAILED|BLOCKED)\b", res_str):
                return True
    elif isinstance(tool_result, str):
        if re.search(r"\bexited with code [1-9]", tool_result):
            return True
        if tool_name == "invoke_subagent":
            if re.search(r"STATUS:\s*(?:FAILED|BLOCKED)\b", tool_result):
                return True
    return False


def handle_post_tool_use(payload: Dict[str, Any]) -> Dict[str, Any]:
    tool_call = payload.get("toolCall") or {}
    tool_name = str(tool_call.get("name") or "")
    state = load_state(payload)
    if tool_name == "invoke_subagent" and payload.get("error"):
        args = tool_call.get("args") or {}
        n = len(_get(args, "Subagents", "subagents", default=[]) or [])
        pending = state.get("pending_spawns", [])
        if n and len(pending) >= n:
            del pending[-n:]
        else:
            state["pending_spawns"] = []
    else:
        sync_subagents(payload, state)

    # Track consecutive failures for the circuit breaker
    if tool_name in ("run_command", "invoke_subagent"):
        if _tool_failed(tool_name, payload):
            state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
        else:
            state["consecutive_failures"] = 0

    save_state(payload, state)
    return {}


def handle_stop(payload: Dict[str, Any]) -> Dict[str, Any]:
    if str(payload.get("terminationReason") or "") in SAFE_TERMINATION_REASONS:
        return {"decision": "allow"}
    if not payload.get("fullyIdle", True):
        return {"decision": "allow"}

    state = load_state(payload)
    sync_subagents(payload, state)
    active = [cid for cid, m in state.get("subagents", {}).items() if m.get("active_branch")]
    active += [m.get("role", "worker") for m in state.get("pending_spawns", []) if m.get("active_branch")]
    if not active:
        save_state(payload, state)
        return {"decision": "allow"}

    # agy increments executionNum after every "continue", so keying on it would
    # nag on every attempt. Remind once per distinct set of live branch workers:
    # a new branch worker earns a new reminder, ignoring the same one does not.
    key = ",".join(sorted(active))
    reminded: List[str] = state.setdefault("stop_reminded_sets", [])
    if key in reminded:
        save_state(payload, state)
        return {"decision": "allow"}
    reminded.append(key)
    save_state(payload, state)
    return {
        "decision": "continue",
        "reason": (
            f"[Argon Orchestrator] Branch worktrees are still active ({', '.join(active)}). "
            "Merge or discard their changes, then call manage_subagents(Action='kill') before finishing."
        ),
    }


def _debug_log(payload: Dict[str, Any], mode: str, result: Dict[str, Any]) -> None:
    """With AGY_ARGON_DEBUG=1, append one JSON line per hook call to the artifact directory."""
    if not _env_flag("AGY_ARGON_DEBUG"):
        return
    path = _state_file(payload)
    if not path:
        return
    tool_call = payload.get("toolCall") or {}
    entry = {
        "mode": mode,
        "tool": tool_call.get("name"),
        "modelName": payload.get("modelName"),
        "invocationNum": payload.get("invocationNum"),
        "executionNum": payload.get("executionNum"),
        "fullyIdle": payload.get("fullyIdle"),
        "terminationReason": payload.get("terminationReason"),
        "payloadKeys": sorted(payload.keys()),
        "result": result,
    }
    try:
        with open(path.with_name("argon_orchestrator_debug.log"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
