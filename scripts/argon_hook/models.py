"""Primary-model detection and settings.json handling."""

import json
import os
import re
import tempfile
from typing import Any, Dict, Optional, Tuple

from .common import ALLOWED_PRIMARY_MODELS, DEFAULT_PRIMARY_MODEL, _env_flag


SMALL_TIER_RE = re.compile(r"\b(flash|lite|mini|nano|haiku)\b", re.IGNORECASE)
FRONTIER_TOKENS = (
    "argon",
    "pro",
    "ultra",
    "claude",
    "opus",
    "sonnet",
    "gpt-4",
    "gpt-5",
    "o1",
    "o3",
    "o4",
    "deepseek",
    "grok",
)


def is_argon_tier(model_name: str) -> bool:
    """True for Argon / Pro / Ultra / Claude / frontier models; False for Flash, Lite, Mini, Nano, Haiku, Auto or empty."""
    normalized = (model_name or "").strip().lower()
    if not normalized or normalized in {"auto", "none", "inherit"}:
        return False
    if SMALL_TIER_RE.search(normalized):
        return False
    return any(token in normalized for token in FRONTIER_TOKENS)


is_frontier_tier = is_argon_tier


def settings_path() -> str:
    override = os.environ.get("AGY_ANTIGRAVITY_SETTINGS", "").strip()
    if override:
        return os.path.expanduser(override)
    return os.path.expanduser("~/.gemini/antigravity-cli/settings.json")


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
    'ok' (Argon/frontier), 'warn' (non-Argon, not changed), 'pinned' (rewritten, opt-in),
    'unknown' (settings file missing/unreadable), 'silenced'.
    """
    preferred = os.environ.get("AGY_ARGON_PRIMARY_MODEL", "").strip()
    target = preferred if (preferred in ALLOWED_PRIMARY_MODELS or is_argon_tier(preferred)) else DEFAULT_PRIMARY_MODEL
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
