#!/usr/bin/env python3
"""Argon Orchestrator lifecycle hooks for Antigravity (agy).

Zero-dependency (Python 3.8+ standard library only).

Modes (first CLI argument):

session-start
    Reads ~/.gemini/antigravity-cli/settings.json and injects the orchestration
    policy notice. By default it only WARNS when the configured primary model is
    not an Argon/Pro-tier model. Set AGY_ARGON_PIN_SETTINGS=1 to opt in to the
    legacy behaviour that rewrites settings.json back to an Argon model.

pre-invocation
    Checks the live session model (payload.modelName) on every turn. If it is not
    Argon/Pro-tier, injects a one-time ephemeral warning per invocation so the
    primary agent asks the user to switch models. Resets per-turn tracking
    (turn_edited_files, consecutive_failures).

pre-tool-use
    invoke_subagent   -> context-classifies every subagent into flash / flash_lite
                         (strong signals always win over lite keywords; mutation
                         prompts never stay on flash_lite) and records pending
                         spawns for lifecycle tracking.
    send_message      -> denies mutation or multi-step (>3 numbered rules) tasks
                         sent to a known flash_lite recipient.
    run_workflow      -> denies scripts whose agent(...) calls omit
                         model="flash" / model="flash_lite".
    define_subagent   -> forces enable_subagent_tools=False (no nested spawning).
    manage_subagents  -> marks killed branch workers as cleaned up.
    circuit breaker   -> denies run_command and invoke_subagent when consecutive
                         failures reach >= 2.
    context hygiene   -> blocks direct inspection of .log and transcript.jsonl
                         files by the primary agent (delegates to flash-lite-scout).
    fast-path budget  -> tracks direct workspace edits (max 3 by default) and blocks
                         direct workspace edit + test-runner loops in the primary agent.
    workspace guard   -> "allow" when cwd, paths and command text stay inside
                         workspacePaths or artifactDirectoryPath; "ask" for
                         paths outside, privilege escalation, destructive or
                         system-altering commands, force pushes, or downloads
                         piped to a shell; fallback returns explicit "allow"
                         pass-through so tools are never denied by hook output.

post-tool-use
    Rolls back pending spawns when invoke_subagent fails; otherwise pairs pending
    spawns with the conversationIds that appear in the transcript. Tracks
    consecutive failures across run_command and invoke_subagent for the circuit breaker.

stop
    When the turn is fully idle and Workspace:"branch" workers are still active,
    asks the primary agent (once per set of live branch workers) to merge/discard and kill them
    before finishing. Never blocks user cancellations, errors, or budget caps.

Environment variables:
    AGY_ARGON_PIN_SETTINGS=1          opt in to rewriting settings.json at session start
    AGY_ARGON_PRIMARY_MODEL=<name>    target model when pinning (default: Gemini 4 Argon (High))
    AGY_ALLOW_NON_ARGON_PRIMARY=1     silence all primary-model warnings
    AGY_ANTIGRAVITY_SETTINGS=<path>   override the settings.json location (tests)
    AGY_ARGON_AUTO_ALLOW=1            enable the workspace scope guard (off by default)
    AGY_ARGON_MAX_DIRECT_EDITS=<int>  max distinct workspace file edits per turn (default: 3)
    AGY_ARGON_ALLOW_DIRECT_LOGS=1     allow primary agent to read raw logs and transcripts
    AGY_ARGON_ALLOW_DIRECT_FASTPATH=1 allow primary agent direct edit-test loops and unlimited edits
    AGY_ARGON_CIRCUIT_BREAKER=0       disable the 2-failure circuit breaker
    AGY_ARGON_LOCK_TIMEOUT=<sec>      per-session state lock wait, fail open (default: 2)
    AGY_ARGON_APPEND_REPORT_FORMAT=0  stop appending the REPORT FORMAT block to subagent prompts
    AGY_ARGON_WORKER_DOWNGRADE=0      keep plain read-only lookups typed flash-worker on flash
    AGY_ARGON_JUDGE_URL=<url>         optional judge for low-confidence routing (falls back to
                                      MITOSIS_JUDGE_URL); AGY_ARGON_JUDGE_THRESHOLD (default 0.9)
    AGY_ARGON_DEBUG=1                 append one JSON line per hook call to
                                      <artifactDirectoryPath>/argon_orchestrator_debug.log
"""

import copy  # noqa: F401
import json
import os  # noqa: F401
import re  # noqa: F401
import sys
import tempfile  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any, Dict, List, Optional, Tuple  # noqa: F401

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from argon_hook import common, guards, handlers, models, report, routing, state, tools  # noqa: F401,E402
from argon_hook.common import (  # noqa: F401,E402
    AGENT_CALL_RE,
    ALLOWED_PRIMARY_MODELS,
    ALLOWED_SUBAGENT_MODELS,
    CONVERSATION_ID_RE,
    DEFAULT_PRIMARY_MODEL,
    LOG_OR_TRANSCRIPT_RE,
    MODEL_KWARG_RE,
    MUTATION_TOOLS,
    NUMBERED_RULE_RE,
    READ_TOOLS,
    SAFE_TERMINATION_REASONS,
    SCOUT_TYPE,
    STATE_FILENAME,
    TEST_RUNNER_RE,
    WORKER_TYPE,
    _allow,
    _ask,
    _deny,
    _env_flag,
    _get,
    is_primary_agent,
)
from argon_hook.models import (  # noqa: F401,E402
    FRONTIER_TOKENS,
    POLICY_NOTICE,
    SMALL_TIER_RE,
    _read_settings,
    _write_settings_atomic,
    check_primary_model_settings,
    is_argon_tier,
    is_frontier_tier,
    settings_path,
)
from argon_hook.routing import (  # noqa: F401,E402
    HEAVY_RE,
    LITE_RE,
    MUTATION_RE,
    classify_subagent,
    classify_subagent_tier,
    decide_subagent,
)
from argon_hook.report import (  # noqa: F401,E402
    MAX_REPORT_BULLET_LINES,
    MAX_SUBAGENT_REPORT_CHARS,
    REPORT_BULLET_RE,
    REPORT_LINE_PATTERNS,
    REPORT_LOG_URI_RE,
    validate_subagent_report,
)
from argon_hook.state import (  # noqa: F401,E402
    LOCK_FILENAME,
    SUBAGENT_ALIVE_STATE,
    _HELD_LOCKS,
    _RECENT_DECISIONS,
    _empty_state,
    _peek_state,
    _release_state_locks,
    _releases_state_locks,
    _state_file,
    _subagent_metadata_dir,
    load_state,
    save_state,
    sync_subagents,
    sync_subagents_from_artifacts,
    sync_subagents_from_transcript,
)
from argon_hook.guards import (  # noqa: F401,E402
    ABS_PATH_TOKEN_RE,
    DOTDOT_TOKEN_RE,
    ESCALATION_RE,
    FORCE_PUSH_RE,
    PATH_ARG_KEYS,
    PATH_LIST_ARG_KEYS,
    PIPE_TO_SHELL_RE,
    RECURSIVE_DELETE_OF_ROOT_RE,
    SYSTEM_ALTERING_RE,
    SYSTEM_PATH_PREFIXES,
    WRITE_TOOL_RE,
    _handle_workspace_guard,
    _within_roots,
    allowed_roots,
    command_stays_in_workspace,
)
from argon_hook.tools import (  # noqa: F401,E402
    _agent_call_bodies,
    _handle_define_subagent,
    _handle_invoke_subagent,
    _handle_manage_subagents,
    _handle_run_workflow,
    _handle_send_message,
    _iter_strings,
    workflow_model_violations,
)
from argon_hook.handlers import (  # noqa: F401,E402
    _debug_log,
    _tool_failed,
    handle_post_tool_use,
    handle_pre_invocation,
    handle_pre_tool_use,
    handle_session_start,
    handle_stop,
)

HANDLERS = {
    "session-start": handle_session_start,
    "pre-invocation": handle_pre_invocation,
    "pre-tool-use": handle_pre_tool_use,
    "post-tool-use": handle_post_tool_use,
    "stop": handle_stop,
}


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "pre-tool-use"
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    handler = HANDLERS.get(mode)
    try:
        result = handler(payload) if handler else {}
    except Exception as exc:  # never break the agent loop because of the guard
        result = {"decision": "allow"} if mode in ("stop", "pre-tool-use") else {}
        sys.stderr.write(f"[argon-orchestrator] {mode} hook error: {exc!r}\n")
    finally:
        _release_state_locks()
    _debug_log(payload, mode, result)

    sys.stdout.write(json.dumps(result) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
