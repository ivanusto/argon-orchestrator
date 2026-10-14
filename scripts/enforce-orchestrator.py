#!/usr/bin/env python3
"""Argon Orchestrator lifecycle hook for Antigravity.

Modes:
1. session-start:
   - Ensures ~/.gemini/antigravity-cli/settings.json pins "Gemini 4 Argon (High)"
     as the primary model (unless AGY_ALLOW_NON_ARGON_PRIMARY=1 is set).
   - Injects a SessionStart systemMessage reminding the primary Argon agent of
     the subagent context-routing policy (flash_lite vs. flash).
2. pre-tool-use:
   - Intercepts `invoke_subagent` calls.
   - Rewrites any subagent whose `Model` is missing, `"inherit"`, or `"pro"`
     (or mismatched with `flash-lite-scout` / `flash-worker`) to `"flash_lite"`
     or `"flash"` based on context analysis of `TypeName`, `Role`, `Workspace`,
     and `Prompt`.
"""

import copy
import json
import os
import re
import sys
import tempfile
from typing import Any, Dict, List, Tuple

PRIMARY_MODEL = "Gemini 4 Argon (High)"
ALLOWED_SUBAGENT_MODELS = {"flash", "flash_lite"}

FLASH_STRONG_PATTERNS = re.compile(
    r"\b("
    r"implement|refactor|edit|modify|write|rewrite|fix|bug|debug|patch|"
    r"test|tests|pytest|unittest|jest|vitest|go\s+test|cargo\s+test|npm\s+test|"
    r"multi-file|across\s+files|call\s*graph|dependency|trace|synthesize|"
    r"benchmark|migration|migrate|create\s+file|update\s+file|build|compile|"
    r"integration|verify\s+fix|deep\s+dive"
    r")\b",
    re.IGNORECASE,
)

FLASH_LITE_PATTERNS = re.compile(
    r"\b("
    r"scout|lookup|look\s+up|find|locate|where\s+is|grep|rg|search\s+for|"
    r"list\s+files|ls|status|git\s+status|log\s+check|check\s+logs|tail|"
    r"format|formatting|lint|single\s+file|line\s+number|quick|count|version|"
    r"check\s+if|exists|summarize\s+file|inspect\s+file"
    r")\b",
    re.IGNORECASE,
)


def ensure_primary_model_pinned() -> Tuple[bool, str]:
    """Ensure settings.json pins Gemini 4 Argon (High) as the primary model."""
    if os.environ.get("AGY_ALLOW_NON_ARGON_PRIMARY") == "1":
        return False, PRIMARY_MODEL

    settings_path = os.path.expanduser("~/.gemini/antigravity-cli/settings.json")
    if not os.path.exists(settings_path):
        return False, PRIMARY_MODEL

    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return False, PRIMARY_MODEL

        current = data.get("model")
        if current == PRIMARY_MODEL:
            return False, PRIMARY_MODEL

        data["model"] = PRIMARY_MODEL
        dir_name = os.path.dirname(settings_path)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".settings.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp_f:
                json.dump(data, tmp_f, indent=2)
                tmp_f.write("\n")
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, settings_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        return True, PRIMARY_MODEL
    except Exception:
        return False, PRIMARY_MODEL


def classify_subagent_tier(subagent: Dict[str, Any]) -> str:
    """Classify a subagent invocation into 'flash_lite' or 'flash' based on context."""
    type_name = str(subagent.get("TypeName") or subagent.get("typeName") or "").strip().lower()
    role = str(subagent.get("Role") or subagent.get("role") or "").strip()
    prompt = str(subagent.get("Prompt") or subagent.get("prompt") or "").strip()
    workspace = str(subagent.get("Workspace") or subagent.get("workspace") or "inherit").strip().lower()
    requested_model = str(subagent.get("Model") or subagent.get("model") or "").strip().lower()

    # 1. Dedicated custom subagent types always pin to their designated tier
    if type_name == "flash-lite-scout":
        return "flash_lite"
    if type_name == "flash-worker":
        return "flash"

    # 2. If the primary Argon agent already explicitly selected 'flash' or 'flash_lite',
    #    honor that choice unless an isolated write workspace ('branch'/'share') or 'self'
    #    was mistakenly paired with 'flash_lite' on a complex code-edit prompt.
    combined_text = f"{role}\n{prompt}"
    has_strong_flash = bool(FLASH_STRONG_PATTERNS.search(combined_text))
    has_flash_lite = bool(FLASH_LITE_PATTERNS.search(combined_text))

    if requested_model in ALLOWED_SUBAGENT_MODELS:
        if requested_model == "flash_lite" and workspace in ("branch", "share") and has_strong_flash:
            return "flash"
        return requested_model

    # 3. Context-based classification when Model is 'inherit', 'pro', or omitted:
    # Write-isolated workspaces or 'self' subagents performing edits/tests -> 'flash'
    if workspace in ("branch", "share"):
        return "flash"

    if has_strong_flash and not (has_flash_lite and len(prompt) < 180 and type_name == "research"):
        return "flash"

    if has_flash_lite and len(prompt) <= 500:
        return "flash_lite"

    if type_name == "research" and len(prompt) <= 320:
        return "flash_lite"

    if type_name == "self" or len(prompt) > 450:
        return "flash"

    return "flash_lite"


def handle_session_start(_payload: Dict[str, Any]) -> Dict[str, Any]:
    updated, pinned_model = ensure_primary_model_pinned()
    pin_note = (
        f"Restored primary model pin to '{pinned_model}' in settings.json."
        if updated
        else f"Primary model pinned to '{pinned_model}'."
    )
    msg = (
        f"[Argon Orchestrator Active] {pin_note} "
        "Subagent pinning is active: delegate quick lookups/searches/log checks/formatting to "
        "`flash_lite` (`TypeName: \"flash-lite-scout\"` or `Model: \"flash_lite\"`) and "
        "multi-file research, test runs, and scoped code edits to `flash` "
        "(`TypeName: \"flash-worker\"` or `Model: \"flash\"`). "
        "Never use `Model: \"inherit\"` or `Model: \"pro\"` on subagents."
    )
    return {
        "injectSteps": [
            {
                "systemMessage": {
                    "systemMessage": msg,
                }
            }
        ]
    }


def handle_pre_tool_use(payload: Dict[str, Any]) -> Dict[str, Any]:
    tool_call = payload.get("toolCall")
    if not isinstance(tool_call, dict):
        return {"decision": "allow"}

    tool_name = str(tool_call.get("name") or "").strip()
    if tool_name != "invoke_subagent":
        return {"decision": "allow"}

    args = tool_call.get("args")
    if not isinstance(args, dict):
        return {"decision": "allow"}

    subagents_key = "Subagents" if "Subagents" in args else ("subagents" if "subagents" in args else None)
    if subagents_key is None or not isinstance(args.get(subagents_key), list):
        return {"decision": "allow"}

    original_subagents: List[ Any ] = args[subagents_key]
    updated_subagents: List[ Any ] = copy.deepcopy(original_subagents)
    changes: List[str] = []

    for idx, item in enumerate(updated_subagents):
        if not isinstance(item, dict):
            continue
        model_key = "model" if ("model" in item and "Model" not in item) else "Model"
        current_model = str(item.get(model_key) or "inherit").strip()
        target_model = classify_subagent_tier(item)

        if current_model != target_model or model_key != "Model":
            role_label = item.get("Role") or item.get("role") or item.get("TypeName") or f"subagent[{idx}]"
            item.pop("model", None)
            item["Model"] = target_model
            changes.append(f"{role_label}: {current_model} -> {target_model}")

    if not changes:
        return {"decision": "allow"}

    return {
        "decision": "allow",
        "reason": "Argon Orchestrator pinned subagent model(s) by context: " + ", ".join(changes),
        "overwrite": {
            subagents_key: updated_subagents,
        },
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

    try:
        if mode == "session-start":
            result = handle_session_start(payload)
        elif mode == "pre-tool-use":
            result = handle_pre_tool_use(payload)
        else:
            result = {}
    except Exception:
        result = {"decision": "allow"} if mode == "pre-tool-use" else {}

    sys.stdout.write(json.dumps(result) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
