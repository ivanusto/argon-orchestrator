"""Workspace scope guard."""

import os
import re
import shlex
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .common import _allow, _ask, _env_flag, _get


# ---------------------------------------------------------------------------
# Workspace scope guard
# ---------------------------------------------------------------------------
# Project-scoped file and command tools are answered "allow" so the primary
# agent is not interrupted for routine work inside its own workspace.
# Interactive sessions only: headless `agy -p` soft-denies every tool that
# would need a prompt regardless of hook output (verified on agy 1.4.0). Anything
# that reaches outside the workspace, looks destructive, or is a tool this
# guard does not understand is left to Antigravity's normal permission flow
# ("ask" or no opinion).

PATH_ARG_KEYS = (
    "TargetFile", "AbsolutePath", "SearchPath", "DirectoryPath", "FilePath",
    "NotebookPath", "Path", "SearchDirectory",
)
PATH_LIST_ARG_KEYS = ("Paths", "Files", "TargetFiles", "Includes")
WRITE_TOOL_RE = re.compile(r"write|replace|edit|create|delete|remove|rename|move|notebook", re.IGNORECASE)

# Absolute prefixes a command may mention without leaving the workspace
# (interpreters, system binaries, scratch space).
SYSTEM_PATH_PREFIXES = (
    "/dev/", "/usr/", "/bin/", "/sbin/", "/lib/", "/opt/", "/proc/",
    "/tmp/", "/private/tmp/", "/private/var/folders/", "/var/folders/",
    "/System/", "/Library/", "/Applications/",
)

# Commands that must always go through the human permission prompt.
ESCALATION_RE = re.compile(
    r"(?:^|[\s;&|(])(?:sudo|doas|su)\b",
    re.IGNORECASE,
)
RECURSIVE_DELETE_OF_ROOT_RE = re.compile(
    r"(?:^|[\s;&|(])rm\s+-[a-zA-Z]*[rf][a-zA-Z]*\s+(?:/|~|\$HOME)(?:\s|$)",
    re.IGNORECASE,
)
SYSTEM_ALTERING_RE = re.compile(
    r"(?:^|[\s;&|(])(?:mkfs(?:\.\w+)?|diskutil\s+erase\w*|shutdown|reboot|"
    r"launchctl\s+(?:unload|remove|bootout)|chown\s+-R|chmod\s+-R\s+\d+\s+/)",
    re.IGNORECASE,
)
FORCE_PUSH_RE = re.compile(r"\bgit\s+push\b[^|;&]*(?:--force\b|\s-f\b)", re.IGNORECASE)
PIPE_TO_SHELL_RE = re.compile(
    r"\b(?:curl|wget)\b[^|]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b", re.IGNORECASE
)
ABS_PATH_TOKEN_RE = re.compile(r"""(?:^|[\s=:"'(])((?:/|~/?|\$HOME/?)[A-Za-z0-9._/\-]*)""")
DOTDOT_TOKEN_RE = re.compile(r"""(?:^|[\s=:"'(])([A-Za-z0-9._\-/]*\.\.[A-Za-z0-9._\-/]*)""")


def allowed_roots(payload: Dict[str, Any]) -> List[Path]:
    roots: List[Path] = []
    for wp in payload.get("workspacePaths") or []:
        try:
            roots.append(Path(os.path.expanduser(str(wp))).resolve())
        except OSError:
            pass
    artifact_dir = payload.get("artifactDirectoryPath")
    if artifact_dir:
        try:
            roots.append(Path(str(artifact_dir)).resolve())
        except OSError:
            pass
    return roots


def _within_roots(target: Any, roots: List[Path]) -> bool:
    if not target or not roots:
        return False
    try:
        text = str(target)
        if text.startswith("file://"):
            text = text[len("file://"):]
        resolved = Path(os.path.expanduser(text)).resolve()
    except (OSError, ValueError):
        return False
    for root in roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


# Commands a scout may run through Bash. Anything else (and any
# output redirection) is denied, so a read-only scout stays read-only.
SCOUT_READ_ONLY_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "rg", "grep", "egrep", "fgrep", "find", "stat", "file",
    "du", "df", "pwd", "which", "tree", "sort", "cut", "jq", "basename",
    "dirname", "realpath", "readlink", "echo", "date", "uname", "nl", "diff", "cmp", "md5sum",
    "sha256sum",
}


SCOUT_GIT_SUBCOMMANDS = {
    "status", "log", "show", "diff", "blame", "branch", "rev-parse", "ls-files", "ls-tree",
    "grep", "describe", "shortlog", "reflog", "remote", "tag", "config",
}


SCOUT_FORBIDDEN_ARGS = {
    "find": {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"},
    "sort": {"-o", "--output"},
    "tree": {"-o"},
    "diff": {"--output"},
    "log": {"--output"},
    "show": {"--output"},
    "branch": {"-d", "-D", "-m", "-M", "-c", "-C", "--delete", "--move", "--copy", "--set-upstream-to", "-u"},
    "tag": {"-d", "--delete", "-a", "-s", "-f"},
    "remote": {"add", "remove", "rm", "rename", "set-url", "prune", "set-head"},
    "config": {"--add", "--unset", "--unset-all", "--replace-all", "--edit", "-e", "--global", "--system"},
    "reflog": {"delete", "expire"},
}


_SCOUT_SEGMENT_SPLIT_RE = re.compile(r"\|\||&&|[|;\n]")


_SCOUT_HARMLESS_REDIRECT_RE = re.compile(r"\d?>\s*/dev/null|2>&1")


def scout_command_is_read_only(command: str) -> Tuple[bool, str]:
    """Check a scout's Bash command against the read-only allowlist."""
    cleaned = _SCOUT_HARMLESS_REDIRECT_RE.sub(" ", command)
    if re.search(r"[<>`]|\$\(", cleaned):
        return False, "redirection or command substitution"
    for segment in _SCOUT_SEGMENT_SPLIT_RE.split(cleaned):
        try:
            words = shlex.split(segment)
        except ValueError:
            return False, "unparseable command"
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            words = words[1:]
        if not words:
            continue
        prog = os.path.basename(words[0])
        if prog == "git":
            sub = next((w for w in words[1:] if not w.startswith("-")), "")
            if sub not in SCOUT_GIT_SUBCOMMANDS:
                return False, f"git {sub or '(none)'} is not read-only"
            if sub == "config" and not any(w in ("--get", "--get-all", "--list", "-l", "--get-regexp") for w in words):
                return False, "git config without --get/--list"
            bad = {w for w in words[2:] for f in SCOUT_FORBIDDEN_ARGS.get(sub, set())
                   if w == f or (f.startswith("--") and w.startswith(f + "="))}
            if sub == "branch" and len([w for w in words[2:] if not w.startswith("-")]) > 0 and not bad:
                bad = {"<create>"}
            if sub == "tag" and len([w for w in words[2:] if not w.startswith("-")]) > 0 and "-l" not in words and "--list" not in words:
                bad = bad or {"<create>"}
            if bad:
                return False, f"git {sub} {sorted(bad)[0]} changes the repository"
            continue
        if prog not in SCOUT_READ_ONLY_COMMANDS:
            return False, f"'{prog}' is not on the scout allowlist"
        forbidden = SCOUT_FORBIDDEN_ARGS.get(prog, set())
        if any(w in forbidden or any(w.startswith(f) for f in forbidden if f.startswith("--")) for w in words[1:]):
            return False, f"'{prog}' with a writing option"
    return True, ""


def command_stays_in_workspace(command: str, cwd: str, roots: List[Path]) -> Tuple[bool, str]:
    """Return (ok, reason). ok is False when the command names a path outside the
    roots or matches a pattern that should always be confirmed by a human."""
    for pattern, why in (
        (ESCALATION_RE, "privilege escalation"),
        (RECURSIVE_DELETE_OF_ROOT_RE, "recursive delete of a root directory"),
        (SYSTEM_ALTERING_RE, "system-altering command"),
        (FORCE_PUSH_RE, "force push"),
        (PIPE_TO_SHELL_RE, "download piped to a shell"),
    ):
        if pattern.search(command):
            return False, why
    home = os.path.expanduser("~")
    for m in ABS_PATH_TOKEN_RE.finditer(command):
        token = m.group(1)
        if token in ("/", "~", "~/", "$HOME", "$HOME/"):
            return False, f"path token {token!r} is outside the workspace"
        expanded = os.path.expanduser(token.replace("$HOME", home))
        if expanded.startswith(SYSTEM_PATH_PREFIXES):
            continue
        if not _within_roots(expanded, roots):
            return False, f"path {token!r} is outside the workspace"
    base = Path(os.path.expanduser(cwd)).resolve() if cwd else (roots[0] if roots else Path.cwd())
    for m in DOTDOT_TOKEN_RE.finditer(command):
        candidate = (base / m.group(1)).resolve()
        if not _within_roots(str(candidate), roots):
            return False, f"relative path {m.group(1)!r} escapes the workspace"
    return True, ""


def _handle_workspace_guard(payload: Dict[str, Any], tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    # Opt-in: the plugin ships with the scope guard off. Set AGY_ARGON_AUTO_ALLOW=1
    # to let project-scoped file and command tools skip the permission prompt.
    # When unset or for tools without path arguments, return an explicit "allow" pass-through.
    if not _env_flag("AGY_ARGON_AUTO_ALLOW"):
        return _allow("Argon Orchestrator: pass-through.")
    roots = allowed_roots(payload)
    if not roots:
        return _allow("Argon Orchestrator: pass-through.")

    if tool_name == "run_command":
        cwd = str(_get(args, "Cwd", "cwd", default="") or "")
        cmd = str(_get(args, "CommandLine", "commandLine", "command", default="") or "")
        if not cmd:
            return _allow("Argon Orchestrator: pass-through.")
        if cwd and not _within_roots(cwd, roots):
            return _ask("Argon Orchestrator: Cwd is outside the workspace.")
        ok, why = command_stays_in_workspace(cmd, cwd, roots)
        if ok:
            return _allow("Argon Orchestrator: command stays inside the workspace.", [f"command({cmd})"])
        return _ask(f"Argon Orchestrator: {why}.")

    targets: List[Any] = []
    for key in PATH_ARG_KEYS:
        value = _get(args, key, key[0].lower() + key[1:], default=None)
        if isinstance(value, str) and value:
            targets.append(value)
    for key in PATH_LIST_ARG_KEYS:
        value = _get(args, key, key[0].lower() + key[1:], default=None)
        if isinstance(value, list):
            targets.extend(v for v in value if isinstance(v, str) and v)
    if not targets:
        return _allow("Argon Orchestrator: pass-through.")
    outside = [t for t in targets if not _within_roots(t, roots)]
    if outside:
        return _ask(f"Argon Orchestrator: {outside[0]!r} is outside the workspace.")
    # "allow" alone does not satisfy Antigravity's permission system (headless
    # runs still auto-deny), so grant the matching read_file/write_file rule
    # for exactly these in-workspace paths.
    action = "write_file" if WRITE_TOOL_RE.search(tool_name) else "read_file"
    return _allow("Argon Orchestrator: path is inside the workspace.", [f"{action}({t})" for t in targets])
