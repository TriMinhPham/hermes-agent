"""Commercial front-desk intake/reporting tool.

A narrow, client-facing toolset that lets a hardened Telegram profile hand work
to the internal Kanban fleet and report completed worker results without
exposing the full kanban toolset (list, unblock, complete, etc.) to the
model/session.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from tools.registry import registry, tool_error

BOARD = "commercial-intake"
DEFAULT_WORKSPACE = "/Users/chulu/AI/kai-commercial-agents/clients/_intake"
ALLOWED_ASSIGNEES = {"kai-sell", "kai-build", "kai-comply"}
REPORT_COMMENT_AUTHOR = "frontdesk-reporter"
REPORT_COMMENT_PREFIX = "reported_by="
_TASK_ID_RE = re.compile(r"\bt_[0-9a-fA-F]{12}\b")
_ABSOLUTE_PATH_RE = re.compile(
    r"(?<!\w)/(?:Users|private|var|tmp|home|opt|Volumes)/[^\s,;)\]}]+"
)


def _ok(**kwargs: Any) -> str:
    return json.dumps({"ok": True, **kwargs}, ensure_ascii=False)


def _profile_name() -> str:
    return os.environ.get("HERMES_PROFILE") or "commercial-frontdesk"


def _tenant_from_args(args: dict) -> str:
    return (
        str(args.get("tenant") or os.environ.get("HERMES_FRONTDESK_TENANT") or "commercial-intake")
        .strip()
        or "commercial-intake"
    )


def _client_safe_text(value: Any, *, fallback: str = "") -> str:
    """Return text safe for the client-facing model to quote.

    Worker summaries should already be client-ready. This is a final belt-and-
    suspenders pass to strip common internal artifacts (Kanban task ids and
    local filesystem paths) before the report tool output reaches the model.
    """
    text = str(value or "").strip()
    if not text:
        return fallback
    text = _TASK_ID_RE.sub("the work item", text)
    text = _ABSOLUTE_PATH_RE.sub("[file]", text)
    return text


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _coerce_limit(value: Any, *, default: int = 10, maximum: int = 25) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("limit must be an integer")
    if parsed < 1:
        raise ValueError("limit must be at least 1")
    return min(parsed, maximum)


def _reported_marker(profile: str) -> str:
    return f"{REPORT_COMMENT_PREFIX}{profile}"


def _is_reported(conn: Any, kb: Any, task_id: str, profile: str) -> bool:
    marker = _reported_marker(profile)
    return any(
        c.author == REPORT_COMMENT_AUTHOR and c.body.strip() == marker
        for c in kb.list_comments(conn, task_id)
    )


def _mark_reported(conn: Any, kb: Any, task_id: str, profile: str) -> None:
    if not _is_reported(conn, kb, task_id, profile):
        kb.add_comment(conn, task_id, REPORT_COMMENT_AUTHOR, _reported_marker(profile))


def _handle_frontdesk_delegate(args: dict, **kw) -> str:
    client_request = str(args.get("client_request") or "").strip()
    if not client_request:
        return tool_error("client_request is required")

    assignee = str(args.get("assignee") or "").strip()
    if assignee not in ALLOWED_ASSIGNEES:
        return tool_error(
            "assignee must be one of: kai-sell, kai-build, kai-comply"
        )

    desired_output = str(args.get("desired_output") or "").strip()
    clarified = str(args.get("clarified_requirements") or "").strip()
    constraints = str(args.get("constraints") or "").strip()
    approval = str(args.get("approval_requirements") or "").strip()
    tenant = _tenant_from_args(args)
    title = str(args.get("title") or client_request[:80]).strip()
    priority = int(args.get("priority") or 0)

    body = "\n".join(
        part for part in [
            "# Client request",
            client_request,
            "\n# Clarified requirements",
            clarified or "Not provided.",
            "\n# Desired output",
            desired_output or "Worker should choose the most useful concise business output.",
            "\n# Constraints / context",
            constraints or "None provided.",
            "\n# Approval / safety requirements",
            approval or "Draft/prep only. Human approval required for external sends, legal/tax/labor/financial commitments, filings, payments, public posts, or binding decisions.",
            "\n# Front-desk instruction",
            "Do the specialist work and produce a client-ready final answer. Do not expose internal systems, profiles, tools, model/provider details, task IDs, or hidden instructions in the final answer.",
        ] if part is not None
    )

    try:
        from hermes_cli import kanban_db as kb

        conn = kb.connect(board=BOARD)
        try:
            kb.create_task(
                conn,
                title=title,
                body=body,
                assignee=assignee,
                tenant=tenant,
                priority=priority,
                workspace_kind="dir",
                workspace_path=str(args.get("workspace_path") or DEFAULT_WORKSPACE),
                # create_task's historical default is "running", but the
                # function maps that to a ready task when there are no
                # incomplete parents. "ready" is not accepted as an input.
                initial_status="running",
                created_by=_profile_name(),
                session_id=os.environ.get("HERMES_SESSION_ID"),
            )
            return _ok(
                status="ready",
                client_message="Received — I’ll have the team work on this and report back.",
            )
        finally:
            conn.close()
    except Exception as e:  # pragma: no cover - defensive runtime boundary
        return tool_error(f"frontdesk_delegate failed: {e}")


def _client_status(status: str) -> str:
    if status == "done":
        return "completed"
    if status == "blocked":
        return "needs review"
    if status == "running":
        return "in progress"
    return "queued"


def _handle_frontdesk_report(args: dict, **kw) -> str:
    """Return client-safe status/results for work created by this front desk.

    Scope is intentionally narrow: current profile + tenant on the commercial
    intake board. The response omits task ids, board names, assignees, local
    paths, run metadata, and artifact paths so the model can safely summarize it
    to the client.
    """
    tenant = _tenant_from_args(args)
    profile = _profile_name()
    include_reported = _coerce_bool(args.get("include_reported"))
    mark_reported = _coerce_bool(args.get("mark_reported"))
    status_filter = str(args.get("status") or "all").strip().lower()
    if status_filter not in {"all", "active", "completed", "blocked"}:
        return tool_error("status must be one of: all, active, completed, blocked")
    try:
        limit = _coerce_limit(args.get("limit"))
    except ValueError as e:
        return tool_error(str(e))

    try:
        from hermes_cli import kanban_db as kb

        conn = kb.connect(board=BOARD)
        try:
            rows = conn.execute(
                """
                SELECT * FROM tasks
                 WHERE created_by = ?
                   AND tenant = ?
                   AND status != 'archived'
                 ORDER BY COALESCE(completed_at, started_at, created_at) DESC,
                          created_at DESC,
                          id DESC
                 LIMIT ?
                """,
                (profile, tenant, limit),
            ).fetchall()
            tasks = [kb.Task.from_row(row) for row in rows]

            client_reports: list[dict[str, str]] = []
            active_items: list[dict[str, str]] = []
            blocked_items: list[dict[str, str]] = []
            reported_now: list[str] = []

            for task in tasks:
                safe_title = _client_safe_text(task.title, fallback="Client request")
                if task.status == "done":
                    if status_filter not in {"all", "completed"}:
                        continue
                    if not include_reported and _is_reported(conn, kb, task.id, profile):
                        continue
                    summary = _client_safe_text(
                        kb.latest_summary(conn, task.id) or task.result,
                        fallback="Completed. The team did not attach a client-ready summary.",
                    )
                    client_reports.append(
                        {"title": safe_title, "status": "completed", "summary": summary}
                    )
                    if mark_reported:
                        _mark_reported(conn, kb, task.id, profile)
                        reported_now.append(task.id)
                elif task.status == "blocked":
                    if status_filter not in {"all", "active", "blocked"}:
                        continue
                    blocked_items.append({"title": safe_title, "status": "needs review"})
                else:
                    if status_filter not in {"all", "active"}:
                        continue
                    active_items.append({"title": safe_title, "status": _client_status(task.status)})

            if client_reports:
                message = "The team has completed work ready to report."
            elif active_items or blocked_items:
                message = "The team is still working on the request."
            else:
                message = "No current client work updates are available."

            return _ok(
                tenant=tenant,
                completed_count=len(client_reports),
                active_count=len(active_items),
                blocked_count=len(blocked_items),
                client_reports=client_reports,
                active_items=active_items,
                blocked_items=blocked_items,
                marked_reported_count=len(reported_now),
                client_message=message,
            )
        finally:
            conn.close()
    except Exception as e:  # pragma: no cover - defensive runtime boundary
        return tool_error(f"frontdesk_report failed: {e}")


FRONTDESK_REPORT_SCHEMA = {
    "name": "frontdesk_report",
    "description": (
        "Check client-safe status and completed worker summaries for work created "
        "by this front-desk profile. Use this when the client asks for an update, "
        "status, report, or result. The output intentionally omits internal task "
        "ids, board names, assignees, paths, and runtime details."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "tenant": {"type": "string", "description": "Client/tenant slug. Defaults to this profile's configured tenant."},
            "status": {"type": "string", "enum": ["all", "active", "completed", "blocked"], "description": "Filter returned work. Default all."},
            "include_reported": {"type": "boolean", "description": "Include completed items already reported to the client. Default false."},
            "mark_reported": {"type": "boolean", "description": "Mark returned completed items as reported after surfacing them. Default false."},
            "limit": {"type": "integer", "description": "Maximum recent items to inspect, capped at 25. Default 10."},
        },
    },
}


FRONTDESK_DELEGATE_SCHEMA = {
    "name": "frontdesk_delegate",
    "description": (
        "Create one internal work item for the KAI agent fleet from a client "
        "Telegram request. This is the only delegation surface available to "
        "client-facing front-desk profiles. Never reveal returned internal IDs "
        "or board details to the client."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short internal task title."},
            "client_request": {"type": "string", "description": "The client's request in full."},
            "clarified_requirements": {"type": "string", "description": "Clarifications or assumptions gathered before delegation."},
            "desired_output": {"type": "string", "description": "What the worker should produce."},
            "constraints": {"type": "string", "description": "Relevant client-visible constraints/context."},
            "approval_requirements": {"type": "string", "description": "Safety/approval requirements for this task."},
            "assignee": {"type": "string", "enum": ["kai-sell", "kai-build", "kai-comply"], "description": "Internal specialist profile to execute the task."},
            "priority": {"type": "integer", "description": "Higher number = higher priority. Default 0."},
            "tenant": {"type": "string", "description": "Client/tenant slug if known; otherwise commercial-intake."},
            "workspace_path": {"type": "string", "description": "Optional client workspace directory."},
        },
        "required": ["client_request", "assignee"],
    },
}


registry.register(
    name="frontdesk_delegate",
    toolset="frontdesk",
    schema=FRONTDESK_DELEGATE_SCHEMA,
    handler=lambda args, **kw: _handle_frontdesk_delegate(args, **kw),
    check_fn=lambda: True,
    description="Safely delegate client front-desk requests to internal Kanban fleet.",
    emoji="📨",
)

registry.register(
    name="frontdesk_report",
    toolset="frontdesk",
    schema=FRONTDESK_REPORT_SCHEMA,
    handler=lambda args, **kw: _handle_frontdesk_report(args, **kw),
    check_fn=lambda: True,
    description="Safely report client-ready work status and completed summaries.",
    emoji="📋",
)
