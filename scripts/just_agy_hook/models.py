"""Primary-model detection and settings.json handling."""

import json
import os
import re
import tempfile
from typing import Any, Dict, Optional, Tuple

from .common import ALLOWED_PRIMARY_MODELS, DEFAULT_PRIMARY_MODEL, _env, _env_flag


SMALL_TIER_RE = re.compile(r"\b(flash|lite|mini|nano|haiku)\b", re.IGNORECASE)
# Frontier primaries: Gemini 4 Argon or Carbon Medium/High and Claude Opus/Sonnet (any version).
# Argon/Carbon Low, Flash, GPT-OSS and every other model are not frontier.
FRONTIER_TOKENS = ("argon", "carbon", "claude", "opus", "sonnet")
ALLOWED_PRIMARY_TEXT = "Gemini 4 Argon or Carbon (Medium or High) or Claude Opus/Sonnet"


def is_frontier_tier(model_name: str) -> bool:
    """True for Gemini 4 Argon/Carbon Medium/High and Claude Opus/Sonnet, by display name or id."""
    normalized = re.sub(r"[_\s]+", "-", (model_name or "").strip().lower())
    if not normalized or normalized in {"auto", "none", "inherit"}:
        return False
    words = re.sub(r"[^a-z0-9.]+", " ", normalized)
    if SMALL_TIER_RE.search(words):
        return False
    if "argon" in words or "carbon" in words:
        return not re.search(r"\blow\b", words)
    return any(token in words for token in ("claude", "opus", "sonnet"))


is_argon_tier = is_frontier_tier  # legacy alias


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
    'ok' (frontier), 'warn' (non-frontier, not changed), 'pinned' (rewritten, opt-in),
    'unknown' (settings file missing/unreadable), 'silenced'.
    """
    preferred = _env("PRIMARY_MODEL").strip()
    target = preferred if (preferred in ALLOWED_PRIMARY_MODELS or is_frontier_tier(preferred)) else DEFAULT_PRIMARY_MODEL
    data, path = _read_settings()
    if data is None:
        return "unknown", target
    current = str(data.get("model") or "").strip()

    if _env_flag("ALLOW_NON_PRIMARY"):
        return "silenced", current or target
    if is_frontier_tier(current) and (not preferred or current == preferred):
        return "ok", current
    if not _env_flag("PIN_SETTINGS"):
        return "warn", current
    try:
        data["model"] = target
        _write_settings_atomic(path, data)
        return "pinned", target
    except Exception:
        return "warn", current


POLICY_NOTICE = (
    "Subagent pinning is active: send to `flash_lite` (`TypeName: \"flash-lite-scout\"`) only reads of "
    "files at paths you name (single-file or symbol inspection at a known path). On agy 1.5+ scouts have "
    "only read_file (no search, listing or shell), so file/symbol location searches, grep/glob, git/log "
    "checks, test runs, web/docs research and edits go to `flash` (`TypeName: \"flash-worker\"`); "
    "always give scouts exact paths. "
    "Never use `Model: \"inherit\"` or `Model: \"pro\"` on subagents. "
    "Every subagent prompt must end with the four-line REPORT FORMAT from rules/AGENTS.md."
)
