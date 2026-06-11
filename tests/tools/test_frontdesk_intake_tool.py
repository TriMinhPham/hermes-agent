from __future__ import annotations

import json


def _isolated_frontdesk(monkeypatch, tmp_path, profile: str = "client-acme-staff"):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PROFILE", profile)
    monkeypatch.setenv("HERMES_SESSION_ID", "session_client_1")
    # Tenant binding is trusted profile config, not a model argument.
    monkeypatch.setenv("HERMES_FRONTDESK_TENANT", "acme")
    monkeypatch.delenv("HERMES_FRONTDESK_WORKSPACE", raising=False)

    from hermes_cli import kanban_db as kb

    kb._INITIALIZED_PATHS.clear()
    kb.init_db(board="commercial-intake")
    return home


def test_frontdesk_delegate_creates_client_scoped_task_without_exposing_internal_id(monkeypatch, tmp_path):
    _isolated_frontdesk(monkeypatch, tmp_path)

    from tools import frontdesk_intake_tool as ft
    from hermes_cli import kanban_db as kb

    out = json.loads(ft._handle_frontdesk_delegate({
        "title": "Prepare GEO audit",
        "client_request": "Please audit our AI search visibility.",
        "assignee": "kai-sell",
    }))

    assert out["ok"] is True
    assert out["client_message"] == "Received — I’ll have the team work on this and report back."
    assert out["status"] == "ready"
    assert "task_id" not in out
    assert "board" not in out
    assert "assignee" not in out

    conn = kb.connect(board="commercial-intake")
    try:
        tasks = kb.list_tasks(conn, tenant="acme", include_archived=True)
    finally:
        conn.close()
    assert len(tasks) == 1
    assert tasks[0].created_by == "client-acme-staff"
    assert tasks[0].session_id == "session_client_1"


def test_frontdesk_report_returns_completed_worker_summary_without_ids_or_paths(monkeypatch, tmp_path):
    _isolated_frontdesk(monkeypatch, tmp_path)

    from tools import frontdesk_intake_tool as ft
    from hermes_cli import kanban_db as kb

    created = json.loads(ft._handle_frontdesk_delegate({
        "title": "Draft LinkedIn post",
        "client_request": "Draft a launch post.",
        "assignee": "kai-sell",
    }))
    assert created["ok"] is True

    conn = kb.connect(board="commercial-intake")
    try:
        task = kb.list_tasks(conn, tenant="acme")[0]
        kb.complete_task(
            conn,
            task.id,
            summary="Client-ready draft: Announce the product in three concise paragraphs.",
            metadata={"artifacts": ["/secret/client/path/draft.md"]},
        )
    finally:
        conn.close()

    report = json.loads(ft._handle_frontdesk_report({}))

    assert report["ok"] is True
    assert report["completed_count"] == 1
    assert report["active_count"] == 0
    assert report["client_reports"] == [
        {
            "title": "Draft LinkedIn post",
            "status": "completed",
            "summary": "Client-ready draft: Announce the product in three concise paragraphs.",
        }
    ]
    rendered = json.dumps(report)
    assert task.id not in rendered
    assert "commercial-intake" not in rendered
    assert "kai-sell" not in rendered
    assert "/secret/client/path" not in rendered


def test_frontdesk_report_scopes_to_current_profile_and_tenant(monkeypatch, tmp_path):
    _isolated_frontdesk(monkeypatch, tmp_path, profile="client-acme-staff")

    from tools import frontdesk_intake_tool as ft
    from hermes_cli import kanban_db as kb

    conn = kb.connect(board="commercial-intake")
    try:
        own = kb.create_task(
            conn,
            title="Own completed work",
            body="client work",
            assignee="kai-build",
            tenant="acme",
            created_by="client-acme-staff",
            initial_status="running",
        )
        kb.complete_task(conn, own, summary="Own client-safe summary")
        other_profile = kb.create_task(
            conn,
            title="Other client work",
            body="must not leak",
            assignee="kai-build",
            tenant="acme",
            created_by="client-other-staff",
            initial_status="running",
        )
        kb.complete_task(conn, other_profile, summary="Other profile secret")
        other_tenant = kb.create_task(
            conn,
            title="Other tenant work",
            body="must not leak",
            assignee="kai-build",
            tenant="otherco",
            created_by="client-acme-staff",
            initial_status="running",
        )
        kb.complete_task(conn, other_tenant, summary="Other tenant secret")
    finally:
        conn.close()

    report = json.loads(ft._handle_frontdesk_report({}))
    rendered = json.dumps(report)

    assert report["completed_count"] == 1
    assert report["client_reports"][0]["summary"] == "Own client-safe summary"
    assert "Other profile secret" not in rendered
    assert "Other tenant secret" not in rendered


def test_frontdesk_report_marks_completed_items_as_reported(monkeypatch, tmp_path):
    _isolated_frontdesk(monkeypatch, tmp_path)

    from tools import frontdesk_intake_tool as ft
    from hermes_cli import kanban_db as kb

    conn = kb.connect(board="commercial-intake")
    try:
        tid = kb.create_task(
            conn,
            title="Completed once",
            body="client work",
            assignee="kai-build",
            tenant="acme",
            created_by="client-acme-staff",
            initial_status="running",
        )
        kb.complete_task(conn, tid, summary="Ready to send once")
    finally:
        conn.close()

    first = json.loads(ft._handle_frontdesk_report({"mark_reported": True}))
    second = json.loads(ft._handle_frontdesk_report({}))
    include = json.loads(ft._handle_frontdesk_report({"include_reported": True}))

    assert first["completed_count"] == 1
    assert second["completed_count"] == 0
    assert include["completed_count"] == 1


def test_frontdesk_delegate_subscribes_origin_chat_for_push_reportback(monkeypatch, tmp_path):
    _isolated_frontdesk(monkeypatch, tmp_path)

    session = {
        "HERMES_SESSION_PLATFORM": "telegram",
        "HERMES_SESSION_CHAT_ID": "chat-42",
        "HERMES_SESSION_THREAD_ID": "7",
        "HERMES_SESSION_USER_ID": "user-9",
    }
    monkeypatch.setattr(
        "gateway.session_context.get_session_env",
        lambda name, default="": session.get(name, default),
    )

    from tools import frontdesk_intake_tool as ft
    from hermes_cli import kanban_db as kb

    out = json.loads(ft._handle_frontdesk_delegate({
        "client_request": "Audit our AI search visibility.",
        "assignee": "kai-sell",
    }))
    assert out["ok"] is True
    assert out["auto_notify"] is True

    conn = kb.connect(board="commercial-intake")
    try:
        task = kb.list_tasks(conn, tenant="acme")[0]
        subs = kb.list_notify_subs(conn, task.id)
    finally:
        conn.close()
    assert len(subs) == 1
    sub = subs[0]
    assert sub["platform"] == "telegram"
    assert sub["chat_id"] == "chat-42"
    assert sub["thread_id"] == "7"
    assert sub["user_id"] == "user-9"
    assert sub["notifier_profile"] == "client-acme-staff"
    assert sub["style"] == "client_safe"


def test_frontdesk_delegate_without_gateway_session_skips_subscription(monkeypatch, tmp_path):
    _isolated_frontdesk(monkeypatch, tmp_path)

    monkeypatch.setattr(
        "gateway.session_context.get_session_env",
        lambda name, default="": default,
    )

    from tools import frontdesk_intake_tool as ft
    from hermes_cli import kanban_db as kb

    out = json.loads(ft._handle_frontdesk_delegate({
        "client_request": "Audit our AI search visibility.",
        "assignee": "kai-sell",
    }))
    assert out["ok"] is True
    assert out["auto_notify"] is False

    conn = kb.connect(board="commercial-intake")
    try:
        task = kb.list_tasks(conn, tenant="acme")[0]
        subs = kb.list_notify_subs(conn, task.id)
    finally:
        conn.close()
    assert subs == []


def test_frontdesk_delegate_survives_subscription_failure(monkeypatch, tmp_path):
    _isolated_frontdesk(monkeypatch, tmp_path)

    monkeypatch.setattr(
        "gateway.session_context.get_session_env",
        lambda name, default="": {"HERMES_SESSION_PLATFORM": "telegram",
                                  "HERMES_SESSION_CHAT_ID": "chat-42"}.get(name, default),
    )

    from tools import frontdesk_intake_tool as ft
    from hermes_cli import kanban_db as kb

    def boom(*args, **kwargs):
        raise RuntimeError("subs table unavailable")

    monkeypatch.setattr(kb, "add_notify_sub", boom)

    out = json.loads(ft._handle_frontdesk_delegate({
        "client_request": "Audit our AI search visibility.",
        "assignee": "kai-sell",
    }))
    assert out["ok"] is True
    assert out["auto_notify"] is False

    conn = kb.connect(board="commercial-intake")
    try:
        assert len(kb.list_tasks(conn, tenant="acme")) == 1
    finally:
        conn.close()


def test_frontdesk_delegate_ignores_model_supplied_tenant_and_workspace(monkeypatch, tmp_path):
    _isolated_frontdesk(monkeypatch, tmp_path)

    from tools import frontdesk_intake_tool as ft
    from hermes_cli import kanban_db as kb

    out = json.loads(ft._handle_frontdesk_delegate({
        "client_request": "Audit our AI search visibility.",
        "assignee": "kai-sell",
        # A prompt-injected client must not be able to choose either of
        # these — tenant and workspace bind to trusted profile config only.
        "tenant": "otherco",
        "workspace_path": "/etc",
    }))
    assert out["ok"] is True

    conn = kb.connect(board="commercial-intake")
    try:
        assert kb.list_tasks(conn, tenant="otherco") == []
        tasks = kb.list_tasks(conn, tenant="acme")
    finally:
        conn.close()
    assert len(tasks) == 1
    assert tasks[0].workspace_path == ft.DEFAULT_WORKSPACE


def test_frontdesk_report_ignores_model_supplied_tenant(monkeypatch, tmp_path):
    _isolated_frontdesk(monkeypatch, tmp_path)

    from tools import frontdesk_intake_tool as ft
    from hermes_cli import kanban_db as kb

    conn = kb.connect(board="commercial-intake")
    try:
        other = kb.create_task(
            conn,
            title="Other tenant work",
            body="must not leak",
            assignee="kai-build",
            tenant="otherco",
            created_by="client-acme-staff",
            initial_status="running",
        )
        kb.complete_task(conn, other, summary="Other tenant secret")
    finally:
        conn.close()

    report = json.loads(ft._handle_frontdesk_report({"tenant": "otherco"}))

    assert report["tenant"] == "acme"
    assert report["completed_count"] == 0
    assert "Other tenant secret" not in json.dumps(report)
