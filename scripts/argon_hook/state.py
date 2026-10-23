"""Per-conversation state and subagent pairing."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .common import CONVERSATION_ID_RE, SCOUT_TYPE, STATE_FILENAME


def _empty_state() -> Dict[str, Any]:
    return {
        "warned_invocations": [],
        "pending_spawns": [],
        "subagents": {},
        "stop_reminded_sets": [],
        "transcript_offset": 0,
        "turn_edited_files": [],
        "consecutive_failures": 0,
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
                if "invoke_subagent" not in line and "Created the following subagents" not in line:
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


SUBAGENT_ALIVE_STATE = "SUBAGENT_STATE_ALIVE"


def _subagent_metadata_dir(payload: Dict[str, Any]) -> Optional[Path]:
    artifact_dir = payload.get("artifactDirectoryPath")
    if not artifact_dir:
        return None
    path = Path(artifact_dir) / ".system_generated" / "subagents"
    return path if path.is_dir() else None


def sync_subagents_from_artifacts(payload: Dict[str, Any], state: Dict[str, Any]) -> bool:
    """Pair pending spawns with <artifactDirectoryPath>/.system_generated/subagents/<cid>.json.

    Antigravity writes one file per spawned subagent with its typeName, role,
    live state and workspace URIs. This is authoritative, so it runs before the
    transcript fallback. Returns True when the directory exists.
    """
    meta_dir = _subagent_metadata_dir(payload)
    if meta_dir is None:
        return False
    pending: List[Dict[str, Any]] = state.setdefault("pending_spawns", [])
    subagents: Dict[str, Dict[str, Any]] = state.setdefault("subagents", {})
    try:
        files = sorted(meta_dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
    except OSError:
        return True
    for file in files:
        try:
            with open(file, encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(meta, dict):
            continue
        cid = str(meta.get("conversationId") or file.stem).strip().lower()
        descriptor = meta.get("subagentDescriptor") or {}
        type_name = str(descriptor.get("typeName") or "").strip()
        role = str(descriptor.get("role") or "").strip()
        alive = str(meta.get("state") or "") == SUBAGENT_ALIVE_STATE
        uris = meta.get("workspaceUris") or []
        in_worktree = any("/worktrees/" in str(u) for u in uris)

        record = subagents.get(cid)
        if record is None:
            # Prefer an exact (type, role) match, then a type match, then FIFO.
            idx = next((i for i, p in enumerate(pending)
                        if p.get("type", "").lower() == type_name.lower() and p.get("role") == role), None)
            if idx is None:
                idx = next((i for i, p in enumerate(pending)
                            if p.get("type", "").lower() == type_name.lower()), None)
            if idx is None and pending:
                idx = 0
            if idx is not None:
                record = pending.pop(idx)
            else:
                record = {
                    "role": role or type_name,
                    "type": type_name,
                    "model": "flash_lite" if type_name.lower() == SCOUT_TYPE else "flash",
                    "workspace": "branch" if in_worktree else "inherit",
                }
            subagents[cid] = record
        if in_worktree:
            record["workspace"] = "branch"
        record["alive"] = alive
        record["active_branch"] = bool(record.get("workspace") == "branch" and alive)
    return True


def sync_subagents(payload: Dict[str, Any], state: Dict[str, Any]) -> None:
    sync_subagents_from_artifacts(payload, state)
    if state.get("pending_spawns"):
        sync_subagents_from_transcript(payload, state)
