"""Shared helpers and constants for the Just agy hook."""

import os
import re
import stat
from typing import Any, Dict, List, NamedTuple, Optional, Tuple


DEFAULT_PRIMARY_MODEL = "Gemini 4 Argon (High)"
ALLOWED_PRIMARY_MODELS = {
    "Gemini 4 Argon (High)",
    "Gemini 4 Argon (Medium)",
    "Gemini 4 Carbon (High)",
    "Gemini 4 Carbon (Medium)",
    "Gemini 4 Hydrogen (High)",
    "Gemini 4 Hydrogen (XHigh)",
    "Orchestrator (Carbon)",
    "Orchestrator (Argon)",
}
ALLOWED_SUBAGENT_MODELS = {"flash", "flash_lite"}
SCOUT_TYPE = "flash-lite-scout"
WORKER_TYPE = "flash-worker"
STATE_FILENAME = "just_agy_orchestrator_state.json"
ENV_PREFIX = "JUST_AGY_"
LEGACY_ENV_PREFIX = "AGY_ARGON_"


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


TEST_RUNNER_RE = re.compile(
    r"\b(pytest|python(?:\d+(?:\.\d+)?)?\s+-m\s+unittest|npm\s+test|pnpm\s+test|yarn\s+test|vitest|jest|cargo\s+test|go\s+test)\b",
    re.IGNORECASE,
)
LOG_OR_TRANSCRIPT_RE = re.compile(
    r"(?:^|[/\\])(?:transcript(?:_full)?\.jsonl|[^/\\]+\.log)$",
    re.IGNORECASE,
)
SMALL_LOG_MAX_BYTES = 16 * 1024


def small_log_max_bytes() -> int:
    """Largest log/transcript the primary may read directly (JUST_AGY_SMALL_LOG_BYTES; 0 disables; invalid -> default)."""
    raw = _env("SMALL_LOG_BYTES").strip()
    if not raw:
        return SMALL_LOG_MAX_BYTES
    try:
        value = int(raw)
    except ValueError:
        return SMALL_LOG_MAX_BYTES
    return SMALL_LOG_MAX_BYTES if value < 0 else value


def is_small_regular_file(target: str) -> bool:
    """True for an absolute path to a regular file of at most small_log_max_bytes(); any OSError -> False."""
    limit = small_log_max_bytes()
    if limit <= 0 or not target or not os.path.isabs(os.path.expanduser(target)):
        return False
    try:
        st = os.stat(os.path.expanduser(target))
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and st.st_size <= limit


READ_TOOLS = {"view_file", "view_file_outline", "view_code_item"}
MUTATION_TOOLS = {
    "write_to_file", "replace_file_content", "multi_replace_file_content",
    "write_file", "edit_file", "create_file",
}


def _allow(reason: str = "Just agy Orchestrator: pass-through.", overrides: Optional[List[str]] = None) -> Dict[str, Any]:
    res: Dict[str, Any] = {"decision": "allow", "reason": reason}
    if overrides:
        res["permissionOverrides"] = overrides
    return res


def _deny(reason: str) -> Dict[str, Any]:
    return {"decision": "deny", "reason": reason}


def _ask(reason: str) -> Dict[str, Any]:
    return {"decision": "ask", "reason": reason}


def is_primary_agent(payload: Dict[str, Any], state: Optional[Dict[str, Any]] = None) -> bool:
    """Return True if caller is the primary orchestrator agent (not a subagent)."""
    if payload.get("isSubagent") is True:
        return False
    cid = str(payload.get("conversationId") or "").strip().lower()
    if state and cid and cid in state.get("subagents", {}):
        return False
    for wp in payload.get("workspacePaths") or []:
        if "/worktrees/" in str(wp).replace("\\", "/"):
            return False
    return True


def _get_env(*names: str, default: str = "") -> str:
    """First non-empty environment variable among `names` (no CLAUDE_/CODEX_ expansion)."""
    for name in names:
        val = os.environ.get(name)
        if val is not None and val.strip() != "":
            return val.strip()
    return default


_LEGACY_ENV_EXTRA = {
    "ALLOW_NON_PRIMARY": ("AGY_ARGON_ALLOW_NON_PRIMARY", "AGY_ALLOW_NON_ARGON_PRIMARY"),
}


def _env_names(name: str, extra_legacy: Tuple[str, ...] = ()) -> Tuple[str, ...]:
    return (ENV_PREFIX + name, LEGACY_ENV_PREFIX + name) + _LEGACY_ENV_EXTRA.get(name, ()) + tuple(extra_legacy)


def _env(name: str, default: str = "", extra_legacy: Tuple[str, ...] = ()) -> str:
    """Read the setting JUST_AGY_<name>, then the legacy AGY_ARGON_<name> (and any extra legacy names)."""
    return _get_env(*_env_names(name, extra_legacy), default=default)


def _env_flag(name: str, extra_legacy: Tuple[str, ...] = ()) -> bool:
    """True when the first non-empty variable for `name` (new prefix first, then legacy) equals "1"."""
    val = _env(name, extra_legacy=extra_legacy)
    return val == "1"


def _get(d: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Case-tolerant lookup: _get(item, "Model", "model")."""
    for key in keys:
        if key in d and d[key] is not None:
            return d[key]
    return default


class Decision(NamedTuple):
    """One routing decision with the evidence behind it.

    `confidence` is a prior per rule (see routing.RULE_CONFIDENCE); `source`
    says which layer decided: rule, judge or default.
    """
    tier: str
    type_override: Optional[str]
    confidence: float
    source: str
    rule: str
    reason: str

    def as_log(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "type_override": self.type_override,
            "confidence": self.confidence,
            "source": self.source,
            "rule": self.rule,
            "reason": self.reason,
        }
