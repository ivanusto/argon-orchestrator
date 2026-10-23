"""Subagent classification."""

import re
from typing import Any, Dict, Optional, Tuple

from .common import ALLOWED_SUBAGENT_MODELS, SCOUT_TYPE, WORKER_TYPE, _get


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
