"""Client-safe rendering of kanban task state.

Shared by the front-desk intake tool (``tools/frontdesk_intake_tool.py``)
and the gateway kanban notifier so the two surfaces can never drift: text
that reaches a client-facing chat is scrubbed of internal artifacts (task
ids, local filesystem paths), and internal lifecycle noise (worker crashes,
dispatcher retries) is suppressed entirely.

A notify subscription opts into this rendering with
``kanban_notify_subs.style = 'client_safe'``.
"""
from __future__ import annotations

import re
from typing import Any, Optional

#: ``kanban_notify_subs.style`` value selecting client-safe rendering.
CLIENT_SAFE_STYLE = "client_safe"

# Current ids are ``t_`` + 4 hex bytes (8 chars, ``kanban_db._new_task_id``);
# legacy boards used 2 bytes and some docs/tests use longer ones. Match the
# whole plausible range rather than one generator vintage.
TASK_ID_RE = re.compile(r"\bt_[0-9a-fA-F]{4,16}\b")
# Any absolute path with at least two segments (/etc/passwd, /mnt/data/x.md,
# /workspace/acme/out.md) — not just a fixed root allowlist. The lookbehind
# rejects matches preceded by a word char, ':' or '/' so URL paths
# (https://host/docs/x) and protocol-relative '//host/...' survive untouched.
ABSOLUTE_PATH_RE = re.compile(r"(?<![\w:/])/[^\s/,;)\]}]+/[^\s,;)\]}]+")

#: Longest client-facing message body; worker summaries should be far
#: shorter, this only guards against runaway handoffs.
MAX_CLIENT_MESSAGE_CHARS = 3500


def client_safe_text(value: Any, *, fallback: str = "") -> str:
    """Return text safe for a client-facing chat to quote.

    Worker summaries should already be client-ready. This is a final
    belt-and-suspenders pass to strip common internal artifacts (kanban
    task ids and local filesystem paths) before text reaches the client.
    """
    text = str(value or "").strip()
    if not text:
        return fallback
    text = TASK_ID_RE.sub("the work item", text)
    text = ABSOLUTE_PATH_RE.sub("[file]", text)
    return text


def format_client_safe_event(
    kind: str,
    *,
    title: str,
    summary: Optional[str] = None,
) -> Optional[str]:
    """Render one terminal task event as a client-facing message.

    Returns ``None`` for event kinds a client should never see
    (``crashed`` / ``timed_out`` are internal retry noise — the dispatcher
    respawns the task and the client only cares about the final outcome).
    """
    safe_title = client_safe_text(title, fallback="Your request")
    if kind == "completed":
        safe_summary = client_safe_text(
            summary,
            fallback="The team has completed this request.",
        )
        msg = f"✅ {safe_title}\n\n{safe_summary}"
    elif kind in ("blocked", "gave_up"):
        msg = (
            f"⏳ {safe_title}\n\n"
            "This request needs additional review from our team before we "
            "can continue. We'll follow up as soon as it moves forward."
        )
    else:
        return None
    return msg[:MAX_CLIENT_MESSAGE_CHARS]
