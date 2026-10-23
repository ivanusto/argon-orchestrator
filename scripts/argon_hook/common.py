"""Shared helpers and constants for the Argon hook."""

import os
import re
from typing import Any, Dict, List, NamedTuple, Optional


DEFAULT_PRIMARY_MODEL = "Gemini 4 Argon (High)"
ALLOWED_PRIMARY_MODELS = {
    "Gemini 4 Argon (High)",
    "Gemini 4 Argon (Medium)",
}
ALLOWED_SUBAGENT_MODELS = {"flash", "flash_lite"}
SCOUT_TYPE = "flash-lite-scout"
WORKER_TYPE = "flash-worker"
STATE_FILENAME = "argon_orchestrator_state.json"


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
READ_TOOLS = {"view_file", "view_file_outline", "view_code_item"}
MUTATION_TOOLS = {"write_to_file", "replace_file_content", "multi_replace_file_content"}


def _allow(reason: str = "Argon Orchestrator: pass-through.", overrides: Optional[List[str]] = None) -> Dict[str, Any]:
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


def _env_flag(*names: str) -> bool:
    """True when the first non-empty variable among `names` equals "1"."""
    for name in names:
        val = os.environ.get(name)
        if val is not None and val.strip() != "":
            return val.strip() == "1"
    return False


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
