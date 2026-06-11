from __future__ import annotations

from hermes_cli.kanban_client_safe import (
    MAX_CLIENT_MESSAGE_CHARS,
    client_safe_text,
    format_client_safe_event,
)


def test_client_safe_text_scrubs_task_ids_and_paths():
    text = "Done t_0123456789ab — report saved to /Users/me/clients/acme/report.docx"
    out = client_safe_text(text)
    assert "t_0123456789ab" not in out
    assert "/Users/" not in out
    assert "the work item" in out
    assert "[file]" in out


def test_client_safe_text_empty_returns_fallback():
    assert client_safe_text("  ", fallback="nothing yet") == "nothing yet"


def test_client_safe_text_scrubs_paths_outside_common_roots():
    for path in ("/etc/passwd", "/mnt/data/report.md", "/workspace/acme/out.md"):
        out = client_safe_text(f"see {path} for details")
        assert path not in out, path
        assert "[file]" in out


def test_client_safe_text_preserves_urls():
    text = (
        "Published at https://example.com/docs/launch-post "
        "and http://cdn.example.com/a/b/c.png"
    )
    assert client_safe_text(text) == text


def test_completed_event_includes_scrubbed_title_and_summary():
    msg = format_client_safe_event(
        "completed",
        title="GEO audit t_0123456789ab",
        summary="Audit finished. Details in /tmp/audit/out.md and t_0123456789ab.",
    )
    assert msg is not None
    assert msg.startswith("✅")
    assert "t_0123456789ab" not in msg
    assert "/tmp/" not in msg
    assert "Audit finished." in msg


def test_completed_event_without_summary_uses_fallback():
    msg = format_client_safe_event("completed", title="GEO audit", summary=None)
    assert msg is not None
    assert "completed this request" in msg


def test_blocked_and_gave_up_render_generic_review_message():
    for kind in ("blocked", "gave_up"):
        msg = format_client_safe_event(kind, title="GEO audit", summary=None)
        assert msg is not None
        assert "additional review" in msg
        # No internal vocabulary leaks into the client message.
        for word in ("Kanban", "blocked", "gave up", "worker", "dispatcher"):
            assert word not in msg


def test_crash_and_timeout_retry_noise_is_suppressed():
    assert format_client_safe_event("crashed", title="GEO audit") is None
    assert format_client_safe_event("timed_out", title="GEO audit") is None
    assert format_client_safe_event("unknown_kind", title="GEO audit") is None


def test_message_is_capped():
    msg = format_client_safe_event(
        "completed", title="big", summary="x" * (MAX_CLIENT_MESSAGE_CHARS * 2),
    )
    assert msg is not None
    assert len(msg) <= MAX_CLIENT_MESSAGE_CHARS
