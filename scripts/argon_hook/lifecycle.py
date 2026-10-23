"""agy-only helpers: subagent detection, report messages, escalation, plugin coexistence.

Verified against agy 1.4.6: every conversation (primary and each subagent) gets its own hook
calls with its own conversationId and artifactDirectoryPath (<brain>/<conversationId>).
A parent's <brain>/<parent>/.system_generated/subagents/<child>.json describes each child, and
delivered reports land in <brain>/<parent>/.system_generated/messages/<uuid>.json.
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .common import SCOUT_TYPE, WORKER_TYPE, Decision, _get_env
from .report import validate_subagent_report
from .state import sync_subagents

MITOSIS_PLUGIN_NAME = "mitosis-orchestrator"
ESCALATION_TTL_SECONDS = 30 * 60
ESCALATION_CALLS = 3
FAILURE_STATUS_RE = re.compile(
    r"^[ \t>*_`-]*STATUS:[ \t*_`]*(FAILED|BLOCKED)\b", re.IGNORECASE | re.MULTILINE
)
STATUS_RE = re.compile(r"^[ \t>*_`-]*STATUS:[ \t*_`]*(COMPLETED|FAILED|BLOCKED)\b", re.IGNORECASE | re.MULTILINE)
SCOUT_BLOCKED_TOOLS = {
    "write_to_file", "replace_file_content", "multi_replace_file_content", "notebook_edit", "run_command",
}
REPORT_FORMAT_HELP = (
    "Resend with exactly these four headers, one per line, at most 1600 characters, plus up to 10 "
    "`- ` bullet lines under NOTES:\n"
    "STATUS: COMPLETED | BLOCKED | FAILED\n"
    "FILES_CHANGED: <comma-separated paths or NONE>\n"
    "VERIFICATION: <one sentence stating the command run and exit result>\n"
    "NOTES: <summary, or file:// log URI if BLOCKED/FAILED>"
)


def enforce_report_enabled() -> bool:
    return _get_env("AGY_ARGON_ENFORCE_REPORT", default="1") != "0"


def escalation_enabled() -> bool:
    return _get_env("AGY_ARGON_ESCALATE", default="1") != "0"


# ---------------------------------------------------------------------------
# Subagent detection
# ---------------------------------------------------------------------------

def _safe_cid(cid: str) -> bool:
    return bool(cid) and "/" not in cid and "\\" not in cid and not cid.startswith(".")


def detect_subagent(payload: Dict[str, Any], state: Optional[Dict[str, Any]] = None) -> Optional[Tuple[str, str, str]]:
    """Return (parentCid, typeName, role) when this conversation is a subagent, else None.

    Looks for <brain>/*/.system_generated/subagents/<conversationId>.json, where <brain> is the
    parent directory of artifactDirectoryPath. A positive result is cached in `state`
    (key "self_subagent") so later hooks of the same conversation skip the glob.
    """
    if state is not None:
        cached = state.get("self_subagent")
        if isinstance(cached, dict) and cached.get("parent"):
            return str(cached["parent"]), str(cached.get("type") or ""), str(cached.get("role") or "")
    cid = str(payload.get("conversationId") or "").strip()
    artifact = str(payload.get("artifactDirectoryPath") or "").strip()
    if not _safe_cid(cid) or not artifact:
        return None
    brain = Path(artifact).parent
    try:
        matches = sorted(brain.glob("*/.system_generated/subagents/%s.json" % cid))
    except (OSError, ValueError):
        return None
    for meta_file in matches:
        try:
            with open(str(meta_file), encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        descriptor = meta.get("subagentDescriptor") or {}
        parent = meta_file.parent.parent.parent.name
        found = (parent, str(descriptor.get("typeName") or "").strip(), str(descriptor.get("role") or "").strip())
        if state is not None:
            state["self_subagent"] = {"parent": found[0], "type": found[1], "role": found[2]}
        return found
    return None


# ---------------------------------------------------------------------------
# Plugin coexistence
# ---------------------------------------------------------------------------

def gemini_config_dir() -> Path:
    override = os.environ.get("AGY_ARGON_GEMINI_CONFIG_DIR", "").strip()
    return Path(os.path.expanduser(override or "~/.gemini/config"))


def mitosis_active() -> bool:
    """True when a plugin named mitosis-orchestrator is installed and not disabled."""
    base = gemini_config_dir()
    found = False
    try:
        for manifest in (base / "plugins").glob("*/plugin.json"):
            try:
                with open(str(manifest), encoding="utf-8") as fh:
                    if json.load(fh).get("name") == MITOSIS_PLUGIN_NAME:
                        found = True
                        break
            except (OSError, ValueError, AttributeError):
                continue
    except OSError:
        return False
    if not found:
        return False
    try:
        with open(str(base / "config.json"), encoding="utf-8") as fh:
            entry = ((json.load(fh).get("plugins") or {}).get(MITOSIS_PLUGIN_NAME)) or {}
        if isinstance(entry, dict) and entry.get("enabled") is False:
            return False
    except (OSError, ValueError, AttributeError):
        pass
    return True


COEXISTENCE_WARNING = (
    "WARNING: the mitosis-orchestrator plugin is also installed and enabled. Both plugins rewrite "
    "the same subagents (model pinning, REPORT FORMAT, routing), so run "
    "`agy plugin disable mitosis-orchestrator` or disable argon-orchestrator."
)


# ---------------------------------------------------------------------------
# Subagent-side report enforcement
# ---------------------------------------------------------------------------

def check_subagent_report(
    state: Dict[str, Any], message: str, enforce: bool = True
) -> Optional[str]:
    """Validate a subagent's report to its parent. Returns a deny reason the first time it is
    invalid; the second invalid attempt is allowed and recorded as report_ok false."""
    problem = validate_subagent_report(message)
    if problem is None:
        state["report_ok"] = True
        return None
    state["report_ok"] = False
    if not enforce:
        return None
    if state.get("report_denied"):
        return None
    state["report_denied"] = True
    return (
        "[Argon Orchestrator] Your report to the parent breaks the required format: "
        f"{problem} {REPORT_FORMAT_HELP}"
    )


# ---------------------------------------------------------------------------
# Parent-side message scan and escalation
# ---------------------------------------------------------------------------

def _messages_dir(payload: Dict[str, Any]) -> Optional[Path]:
    artifact = payload.get("artifactDirectoryPath")
    if not artifact:
        return None
    path = Path(str(artifact)) / ".system_generated" / "messages"
    return path if path.is_dir() else None


def _sender_meta(payload: Dict[str, Any], sender: str) -> Dict[str, str]:
    if not _safe_cid(sender):
        return {}
    meta_file = Path(str(payload.get("artifactDirectoryPath"))) / ".system_generated" / "subagents" / ("%s.json" % sender)
    try:
        with open(str(meta_file), encoding="utf-8") as fh:
            meta = json.load(fh)
        descriptor = (meta or {}).get("subagentDescriptor") or {}
        return {"type": str(descriptor.get("typeName") or ""), "role": str(descriptor.get("role") or "")}
    except (OSError, ValueError, AttributeError):
        return {}


def report_failure_status(text: str) -> Optional[str]:
    m = FAILURE_STATUS_RE.search(text or "")
    return m.group(1).upper() if m else None


def live_escalation(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    esc = state.get("escalation")
    if not isinstance(esc, dict):
        state.pop("escalation", None)
        return None
    try:
        expired = int(esc.get("calls_left") or 0) <= 0 or time.time() - float(esc.get("ts") or 0) > ESCALATION_TTL_SECONDS
    except (TypeError, ValueError):
        expired = True
    if expired or not escalation_enabled():
        state.pop("escalation", None)
        return None
    return esc


def scan_new_messages(payload: Dict[str, Any], state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Process report messages the primary has not seen yet. Mutates `state`; the caller saves it.

    Per sender it records report_ok/status on the spawn record, queues one notice for an
    invalid report (state["invalid_report_senders"]) and arms a scout->flash escalation when a
    scout reported FAILED or BLOCKED. Returns one event dict per new message.
    """
    msg_dir = _messages_dir(payload)
    if msg_dir is None:
        return []
    me = str(payload.get("conversationId") or "")
    first_scan = "seen_messages" not in state
    seen: List[str] = state.setdefault("seen_messages", [])
    try:
        files = sorted(msg_dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
    except OSError:
        return []
    if first_scan and not state.get("pending_spawns") and not state.get("subagents"):
        # Plugin enabled mid-conversation: older reports must not arm an escalation.
        seen.extend(f.stem for f in files if f.name != "read.json")
        del seen[:-500]
        return []
    fresh = []
    for f in files:
        if f.name == "read.json" or f.stem in seen:
            continue
        fresh.append(f)
    if not fresh:
        return []
    sync_subagents(payload, state)
    subagents: Dict[str, Dict[str, Any]] = state.setdefault("subagents", {})
    events: List[Dict[str, Any]] = []
    for f in fresh:
        try:
            with open(str(f), encoding="utf-8") as fh:
                msg = json.load(fh)
        except (OSError, ValueError):
            continue  # possibly half-written; retry next time
        seen.append(f.stem)
        if not isinstance(msg, dict):
            continue
        sender = str(msg.get("sender") or "").strip()
        if not sender or (me and str(msg.get("recipient") or "") not in ("", me)):
            continue
        content = str(msg.get("content") or "")
        meta = _sender_meta(payload, sender)
        if not meta and sender.lower() not in subagents:
            continue  # not one of our subagents
        record = subagents.get(sender.lower()) or subagents.get(sender)
        if record is None:
            record = subagents.setdefault(sender.lower(), {
                "role": meta.get("role", ""), "type": meta.get("type", ""),
                "model": "flash_lite" if meta.get("type", "").lower() == SCOUT_TYPE else "flash",
            })
        type_name = meta.get("type") or str(record.get("type") or "")
        is_scout = type_name.lower() == SCOUT_TYPE or record.get("model") == "flash_lite"
        problem = validate_subagent_report(content)
        status_m = STATUS_RE.search(content)
        status = status_m.group(1).upper() if status_m else ""
        record["report_ok"] = problem is None
        record["status"] = status
        label = record.get("role") or meta.get("role") or type_name or sender[:8]
        events.append({"sender": sender, "type": type_name, "label": str(label), "status": status,
                       "report_ok": problem is None, "problem": problem})
        if problem is not None and not record.get("report_notified"):
            queue = state.setdefault("invalid_report_senders", [])
            queue.append({"sender": sender, "label": str(label), "problem": problem})
            record["report_notified"] = True
        if (is_scout and status in ("FAILED", "BLOCKED") and escalation_enabled()
                and not record.get("escalation_sent")):
            record["escalation_sent"] = True
            state["escalation"] = {
                "from": "flash_lite", "to": "flash", "ts": time.time(),
                "calls_left": ESCALATION_CALLS, "source": "%s (%s)" % (label, sender[:8]),
            }
    del seen[:-500]
    return events


def apply_escalation(
    state: Dict[str, Any], decisions: List[Decision]
) -> Tuple[List[Decision], Optional[Dict[str, str]]]:
    """Run the items of ONE invoke_subagent call through the pending escalation.

    The first scout-tier item becomes flash / flash-worker (rule "escalation") and the
    escalation is consumed. If nothing in the call was scout-tier, one of the 3 allowed
    invoke_subagent calls is used up. Returns (decisions, info-or-None). Mutates `state`.
    """
    esc = live_escalation(state)
    if esc is None:
        return decisions, None
    src = str(esc.get("source") or "a scout")
    out = list(decisions)
    for i, d in enumerate(out):
        if d.tier == "flash_lite":
            out[i] = d._replace(
                tier="flash", type_override=WORKER_TYPE, confidence=1.0, source="rule", rule="escalation",
                reason=f"scout {src} reported FAILED/BLOCKED; re-dispatched on flash-worker",
            )
            state.pop("escalation", None)
            return out, {"source": src, "index": str(i)}
    esc["calls_left"] = int(esc.get("calls_left") or 0) - 1
    if esc["calls_left"] <= 0:
        state.pop("escalation", None)
    return out, None
