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
    primary agent asks the user to switch models. This catches UI model switches
    that happen after the session started.

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

post-tool-use
    Rolls back pending spawns when invoke_subagent fails; otherwise pairs pending
    spawns with the conversationIds that appear in the transcript.

stop
    When the turn is fully idle and Workspace:"branch" workers are still active,
    asks the primary agent (once per execution) to merge/discard and kill them
    before finishing. Never blocks user cancellations, errors, or budget caps.

Environment variables:
    AGY_ARGON_PIN_SETTINGS=1        opt in to rewriting settings.json at session start
    AGY_ARGON_PRIMARY_MODEL=<name>  target model when pinning (default: Gemini 4 Argon (High))
    AGY_ALLOW_NON_ARGON_PRIMARY=1   silence all primary-model warnings
    AGY_ANTIGRAVITY_SETTINGS=<path> override the settings.json location (tests)
"""

import copy
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_PRIMARY_MODEL = "Gemini 4 Argon (High)"
ALLOWED_PRIMARY_MODELS = {
    "Gemini 4 Argon (High)",
    "Gemini 4 Argon (Medium)",
}
ALLOWED_SUBAGENT_MODELS = {"flash", "flash_lite"}
SCOUT_TYPE = "flash-lite-scout"
WORKER_TYPE = "flash-worker"
STATE_FILENAME = "argon_orchestrator_state.json"

# ---------------------------------------------------------------------------
# Classification vocabularies
# ---------------------------------------------------------------------------

# Prompts that ask the subagent to change the workspace. These NEVER run on
# flash_lite, whatever the Workspace mode or the requested Model.
MUTATION_RE = re.compile(
    r"\b("
    r"implement|refactor|rewrite|edit|modify|patch|fix(?:es|ing)?|"
    r"write (?:a |the |new )?(?:file|test|tests|code|module|function|class)|"
    r"create (?:a |the |new )?(?:file|test|tests|module|function|class|script)|"
    r"update (?:the )?(?:file|files|config|test|tests|code)|"
    r"delete|remove|rename|replace|insert|append|"
    r"migrate|migration|add (?:a |the |new )?(?:test|tests|file|function|method|check)"
    r")\b",
    re.IGNORECASE,
)

# Moderate-complexity work that needs flash even when read-only.
HEAVY_RE = re.compile(
    r"\b("
    r"tests?|pytest|unittest|jest|vitest|go test|cargo test|npm test|"
    r"debug|bug|root[- ]cause|multi-file|across (?:files|modules|packages)|"
    r"call[- ]?graph|dependency|dependencies|trace|synthesi[sz]e|"
    r"benchmark|build|compile|integration|deep[- ]dive|"
    r"search_web|read_url_content|documentation|docs? (?:for|on|about)"
    r")\b",
    re.IGNORECASE,
)

# Bounded single-hop lookups. Deliberately narrow: bare words such as
# "find", "ls", "count", "status" or "version" are too common in English and
# produced false flash_lite routing in 1.1.0.
LITE_RE = re.compile(
    r"\b("
    r"scout|look ?up|locate|where is|where are|"
    r"grep|ripgrep|glob|search for|find_by_name|grep_search|list_dir|view_file|"
    r"list (?:the |all )?files|list (?:the )?directory|"
    r"git status|git log|git blame|check (?:the )?logs?|tail (?:the )?logs?|"
    r"line numbers?|single file|one file|"
    r"summari[sz]e (?:this |the )?(?:file|readme|config)|"
    r"inspect (?:this |the )?(?:file|config)|"
    r"(?:file|path|symbol|function|class) exists?|does .{1,40} exist"
    r")\b",
    re.IGNORECASE,
)

NUMBERED_RULE_RE = re.compile(r"(?m)^\s*[0-9]+[.)]\s+")
CONVERSATION_ID_RE = re.compile(
    r'\\*"conversationId\\*"\s*:\s*\\*"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\\*"',
    re.I,
)
AGENT_CALL_RE = re.compile(r"\bagent\s*\(")
MODEL_KWARG_RE = re.compile(r"""\bmodel\s*=\s*(?:["']([^"']*)["']|([A-Za-z_][A-Za-z0-9_.]*))""")

SAFE_TERMINATION_REASONS = {
    "USER_CANCELED",
    "ERROR",
    "MAX_INVOCATIONS",
    "MAX_FORCED_INVOCATIONS",
    "MAX_TOKEN_BUDGET_EXCEEDED",
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def is_argon_tier(model_name: str) -> bool:
    """True for Argon / Pro / Ultra models; False for Flash, Lite, Auto or empty."""
    normalized = (model_name or "").strip().lower()
    if not normalized or normalized in {"auto", "none", "inherit"}:
        return False
    if "flash" in normalized or "lite" in normalized:
        return False
    return any(token in normalized for token in ("argon", "pro", "ultra"))


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip() == "1"


def settings_path() -> str:
    override = os.environ.get("AGY_ANTIGRAVITY_SETTINGS", "").strip()
    if override:
        return os.path.expanduser(override)
    return os.path.expanduser("~/.gemini/antigravity-cli/settings.json")


def _get(d: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Case-tolerant lookup: _get(item, "Model", "model")."""
    for key in keys:
        if key in d and d[key] is not None:
            return d[key]
    return default


# ---------------------------------------------------------------------------
# State (per conversation, lives in the artifact directory)
# ---------------------------------------------------------------------------

def _empty_state() -> Dict[str, Any]:
    return {
        "warned_invocations": [],
        "pending_spawns": [],
        "subagents": {},
        "stop_reminded_executions": [],
        "transcript_offset": 0,
    }


def _state_file(payload: Dict[str, Any]) -> Optional[Path]:
    artifact_dir = payload.get("artifactDirectoryPath")
    if artifact_dir:
        base = Path(artifact_dir)
    else:
        cid = str(payload.get("conversationId") or "default")
        base = Path(tempfile.gettempdir()) / "argon-orchestrator" / cid
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return base / STATE_FILENAME


def load_state(payload: Dict[str, Any]) -> Dict[str, Any]:
    path = _state_file(payload)
    state = _empty_state()
    if path and path.exists():
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                state.update(data)
        except (OSError, json.JSONDecodeError):
            pass
    return state


def save_state(payload: Dict[str, Any], state: Dict[str, Any]) -> None:
    path = _state_file(payload)
    if not path:
        return
    try:
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        pass


def sync_subagents_from_transcript(payload: Dict[str, Any], state: Dict[str, Any]) -> None:
    """Pair pending spawns (FIFO) with new subagent conversationIds in the transcript.

    Only lines mentioning invoke_subagent are inspected, so unrelated UUIDs in
    the transcript (artifacts, task ids, the parent conversation) are ignored.
    Reading resumes from the last byte offset to stay cheap on long sessions.
    """
    transcript_path = payload.get("transcriptPath")
    if not transcript_path or not os.path.exists(transcript_path):
        return
    pending: List[Dict[str, Any]] = state.setdefault("pending_spawns", [])
    subagents: Dict[str, Dict[str, Any]] = state.setdefault("subagents", {})
    parent_cid = str(payload.get("conversationId") or "").lower()
    offset = int(state.get("transcript_offset") or 0)
    try:
        size = os.path.getsize(transcript_path)
        if offset > size:
            offset = 0
        with open(transcript_path, "rb") as fh:
            fh.seek(offset)
            for raw in fh:
                offset += len(raw)
                if not pending:
                    continue
                line = raw.decode("utf-8", errors="replace")
                if "invoke_subagent" not in line:
                    continue
                for match in CONVERSATION_ID_RE.finditer(line):
                    cid = match.group(1).lower()
                    if cid == parent_cid or cid in subagents:
                        continue
                    if not pending:
                        break
                    subagents[cid] = pending.pop(0)
        state["transcript_offset"] = offset
    except OSError:
        return


# ---------------------------------------------------------------------------
# session-start
# ---------------------------------------------------------------------------

def _read_settings() -> Tuple[Optional[Dict[str, Any]], str]:
    path = settings_path()
    if not os.path.exists(path):
        return None, path
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return (data if isinstance(data, dict) else None), path
    except (OSError, json.JSONDecodeError):
        return None, path


def _write_settings_atomic(path: str, data: Dict[str, Any]) -> None:
    dir_name = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".settings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_f:
            json.dump(data, tmp_f, indent=2)
            tmp_f.write("\n")
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def check_primary_model_settings() -> Tuple[str, str]:
    """Return (status, model) where status is one of:
    'ok' (Argon), 'warn' (non-Argon, not changed), 'pinned' (rewritten, opt-in),
    'unknown' (settings file missing/unreadable), 'silenced'.
    """
    preferred = os.environ.get("AGY_ARGON_PRIMARY_MODEL", "").strip()
    target = preferred if preferred in ALLOWED_PRIMARY_MODELS else DEFAULT_PRIMARY_MODEL
    data, path = _read_settings()
    if data is None:
        return "unknown", target
    current = str(data.get("model") or "").strip()

    if _env_flag("AGY_ALLOW_NON_ARGON_PRIMARY"):
        return "silenced", current or target
    if is_argon_tier(current) and (not preferred or current == preferred):
        return "ok", current
    if not _env_flag("AGY_ARGON_PIN_SETTINGS"):
        return "warn", current
    try:
        data["model"] = target
        _write_settings_atomic(path, data)
        return "pinned", target
    except Exception:
        return "warn", current


POLICY_NOTICE = (
    "Subagent pinning is active: delegate single-hop lookups (grep/glob, file or symbol "
    "location, git/log checks, single-file inspection) to `flash_lite` "
    "(`TypeName: \"flash-lite-scout\"`) and multi-file research, test runs, web/docs "
    "research and scoped code edits to `flash` (`TypeName: \"flash-worker\"`). "
    "Never use `Model: \"inherit\"` or `Model: \"pro\"` on subagents. "
    "Every subagent prompt must end with the four-line REPORT FORMAT from rules/AGENTS.md."
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
    model_name = str(payload.get("modelName") or "")
    if not model_name or is_argon_tier(model_name) or _env_flag("AGY_ALLOW_NON_ARGON_PRIMARY"):
        return {}
    state = load_state(payload)
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


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify_subagent(subagent: Dict[str, Any]) -> Tuple[str, Optional[str], str]:
    """Return (model, type_override_or_None, reason).

    Precedence (first match wins):
      1. flash-worker type                      -> flash
      2. mutation verbs in prompt               -> flash  (scout type is upgraded to worker)
      3. flash-lite-scout type                  -> flash_lite
      4. Workspace branch/share                 -> flash
      5. heavy signals (tests, debug, trace...) -> flash  (beats lite keywords)
      6. TypeName self (full tool set)          -> flash
      7. explicit Model flash/flash_lite        -> honoured
      8. lite signals, prompt <= 600 chars      -> flash_lite
      9. prompt > 450 chars                     -> flash
     10. short prompt (<= 320 chars)            -> flash_lite
     11. otherwise                              -> flash
    """
    type_name = str(_get(subagent, "TypeName", "typeName", default="")).strip().lower()
    role = str(_get(subagent, "Role", "role", default="")).strip()
    prompt = str(_get(subagent, "Prompt", "prompt", default="")).strip()
    workspace = str(_get(subagent, "Workspace", "workspace", default="inherit")).strip().lower()
    requested = str(_get(subagent, "Model", "model", default="")).strip().lower()
    text = f"{role}\n{prompt}"

    mutation = MUTATION_RE.search(text)
    heavy = HEAVY_RE.search(text)
    lite = LITE_RE.search(text)

    if type_name == WORKER_TYPE:
        return "flash", None, "flash-worker type"
    if mutation:
        if type_name == SCOUT_TYPE:
            return "flash", WORKER_TYPE, f"mutation verb '{mutation.group(0)}' on read-only scout; upgraded to flash-worker"
        return "flash", None, f"mutation verb '{mutation.group(0)}'"
    if type_name == SCOUT_TYPE:
        return "flash_lite", None, "flash-lite-scout type"
    if workspace in ("branch", "share"):
        return "flash", None, f"Workspace '{workspace}' implies writes"
    if heavy:
        return "flash", None, f"heavy signal '{heavy.group(0)}'"
    if type_name == "self":
        return "flash", None, "self type carries the full tool set"
    if requested in ALLOWED_SUBAGENT_MODELS:
        return requested, None, "explicit model honoured"
    if lite and len(prompt) <= 600:
        return "flash_lite", None, f"lite signal '{lite.group(0)}'"
    if len(prompt) > 450:
        return "flash", None, "long prompt"
    if len(prompt) <= 320:
        return "flash_lite", None, "short prompt without heavy signals"
    return "flash", None, "default"


def classify_subagent_tier(subagent: Dict[str, Any]) -> str:
    """Backwards-compatible wrapper returning only the model tier."""
    return classify_subagent(subagent)[0]


# ---------------------------------------------------------------------------
# pre-tool-use handlers
# ---------------------------------------------------------------------------

def _handle_invoke_subagent(payload: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    key = "Subagents" if "Subagents" in args else ("subagents" if "subagents" in args else None)
    if key is None or not isinstance(args.get(key), list):
        return {"decision": "allow"}
    if not args[key]:
        return {"decision": "deny", "reason": "Subagents list cannot be empty."}

    updated: List[Any] = copy.deepcopy(args[key])
    changes: List[str] = []
    spawns: List[Dict[str, Any]] = []

    for idx, item in enumerate(updated):
        if not isinstance(item, dict):
            continue
        current_model = str(_get(item, "Model", "model", default="inherit")).strip()
        current_type = str(_get(item, "TypeName", "typeName", default="")).strip()
        label = _get(item, "Role", "role", default=None) or current_type or f"subagent[{idx}]"
        prompt = str(_get(item, "Prompt", "prompt", default="")).strip()
        if not prompt:
            return {"decision": "deny", "reason": f"Subagent '{label}' has an empty Prompt."}

        target_model, type_override, reason = classify_subagent(item)
        if current_model != target_model or "model" in item:
            item.pop("model", None)
            item["Model"] = target_model
            changes.append(f"{label}: {current_model} -> {target_model} ({reason})")
        if type_override and current_type.lower() != type_override:
            item.pop("typeName", None)
            item["TypeName"] = type_override
            changes.append(f"{label}: TypeName {current_type} -> {type_override}")

        workspace = str(_get(item, "Workspace", "workspace", default="inherit")).strip().lower()
        spawns.append(
            {
                "role": str(label),
                "type": str(_get(item, "TypeName", "typeName", default="")),
                "model": target_model,
                "workspace": workspace,
                "active_branch": workspace == "branch",
            }
        )

    state = load_state(payload)
    state.setdefault("pending_spawns", []).extend(spawns)
    save_state(payload, state)

    if not changes:
        return {"decision": "allow"}
    return {
        "decision": "allow",
        "reason": "Argon Orchestrator pinned subagent model(s): " + "; ".join(changes),
        "overwrite": {key: updated},
    }


def _handle_send_message(payload: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    recipient = str(_get(args, "Recipient", "recipient", default="")).strip().lower()
    message = str(_get(args, "Message", "message", default=""))
    if not message.strip():
        return {"decision": "deny", "reason": "send_message.Message cannot be empty."}

    state = load_state(payload)
    sync_subagents_from_transcript(payload, state)
    save_state(payload, state)

    info = state.get("subagents", {}).get(recipient)
    if not info or info.get("model") != "flash_lite":
        return {"decision": "allow"}

    mutation = MUTATION_RE.search(message)
    rule_count = len(NUMBERED_RULE_RE.findall(message))
    if mutation or rule_count > 3:
        why = f"mutation verb '{mutation.group(0)}'" if mutation else f"{rule_count} numbered steps"
        return {
            "decision": "deny",
            "reason": (
                f"Recipient '{recipient}' is a flash_lite '{info.get('type') or SCOUT_TYPE}' "
                f"subagent ({why}). Do not send code changes or multi-step (>3 rules) tasks to "
                "flash_lite. Invoke a flash-worker subagent instead."
            ),
        }
    return {"decision": "allow"}


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _iter_strings(v)


def _agent_call_bodies(script: str) -> List[str]:
    """Return the argument text of every top-level agent(...) call (balanced parens)."""
    bodies = []
    for m in AGENT_CALL_RE.finditer(script):
        depth, i, start = 1, m.end(), m.end()
        while i < len(script) and depth:
            ch = script[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            i += 1
        bodies.append(script[start : i - 1] if depth == 0 else script[start:])
    return bodies


def workflow_model_violations(script: str) -> List[str]:
    violations = []
    for n, body in enumerate(_agent_call_bodies(script), 1):
        m = MODEL_KWARG_RE.search(body)
        if not m:
            violations.append(f"agent() call #{n} has no model= argument")
            continue
        literal, ident = m.group(1), m.group(2)
        if literal is not None and literal.strip().lower() not in ALLOWED_SUBAGENT_MODELS:
            violations.append(f"agent() call #{n} uses model={literal!r}")
        # A bare identifier (variable) is accepted; it cannot be checked statically.
    return violations


def _handle_run_workflow(args: Dict[str, Any]) -> Dict[str, Any]:
    violations: List[str] = []
    for text in _iter_strings(args):
        if "agent(" in text or "agent (" in text:
            violations.extend(workflow_model_violations(text))
    if not violations:
        return {"decision": "allow"}
    return {
        "decision": "deny",
        "reason": (
            "run_workflow script must pass model=\"flash\" or model=\"flash_lite\" to every "
            "agent(...) call: " + "; ".join(violations[:5])
        ),
    }


def _handle_define_subagent(args: Dict[str, Any]) -> Dict[str, Any]:
    if args.get("enable_subagent_tools") is True:
        return {
            "decision": "allow",
            "reason": "Argon Orchestrator: subagents may not spawn subagents (enable_subagent_tools forced to false).",
            "overwrite": {"enable_subagent_tools": False},
        }
    return {"decision": "allow"}


def _handle_manage_subagents(payload: Dict[str, Any], args: Dict[str, Any]) -> Dict[str, Any]:
    action = str(_get(args, "Action", "action", default="")).strip().lower()
    if action not in {"kill", "kill_all"}:
        return {"decision": "allow"}
    state = load_state(payload)
    sync_subagents_from_transcript(payload, state)
    subagents = state.setdefault("subagents", {})
    pending = state.setdefault("pending_spawns", [])
    if action == "kill_all":
        for meta in subagents.values():
            meta["active_branch"] = False
        for meta in pending:
            meta["active_branch"] = False
    else:
        targets = {str(c).strip().lower() for c in (_get(args, "ConversationIds", "conversationIds", default=[]) or [])}
        unmatched = 0
        for cid in targets:
            if cid in subagents:
                subagents[cid]["active_branch"] = False
            else:
                unmatched += 1
        # Kills that target spawns we never paired with a conversationId.
        for meta in pending:
            if unmatched <= 0:
                break
            if meta.get("active_branch"):
                meta["active_branch"] = False
                unmatched -= 1
    save_state(payload, state)
    return {"decision": "allow"}


def handle_pre_tool_use(payload: Dict[str, Any]) -> Dict[str, Any]:
    tool_call = payload.get("toolCall")
    if not isinstance(tool_call, dict):
        return {"decision": "allow"}
    tool_name = str(tool_call.get("name") or "").strip()
    args = tool_call.get("args")
    if not isinstance(args, dict):
        return {"decision": "allow"}

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
    return {"decision": "allow"}


# ---------------------------------------------------------------------------
# post-tool-use / stop
# ---------------------------------------------------------------------------

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
        sync_subagents_from_transcript(payload, state)
    save_state(payload, state)
    return {}


def handle_stop(payload: Dict[str, Any]) -> Dict[str, Any]:
    if str(payload.get("terminationReason") or "") in SAFE_TERMINATION_REASONS:
        return {"decision": "allow"}
    if not payload.get("fullyIdle", True):
        return {"decision": "allow"}

    state = load_state(payload)
    sync_subagents_from_transcript(payload, state)
    active = [cid for cid, m in state.get("subagents", {}).items() if m.get("active_branch")]
    active += [m.get("role", "worker") for m in state.get("pending_spawns", []) if m.get("active_branch")]
    if not active:
        save_state(payload, state)
        return {"decision": "allow"}

    key = f"{payload.get('conversationId', '')}:{payload.get('executionNum', 0)}"
    reminded: List[str] = state.setdefault("stop_reminded_executions", [])
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


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

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
        result = {"decision": "allow"} if mode in ("pre-tool-use", "stop") else {}
        sys.stderr.write(f"[argon-orchestrator] {mode} hook error: {exc!r}\n")

    sys.stdout.write(json.dumps(result) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
