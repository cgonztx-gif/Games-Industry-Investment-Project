from types import SimpleNamespace

from agents.token_tracking import (
    record_usage,
    record_usage_from_message,
    token_tracked_step,
)


def test_records_usage_within_step(capsys):
    @token_tracked_step("worker_a")
    def run():
        record_usage("claude-haiku-4-5-20251001", 100, 20)
        record_usage("claude-haiku-4-5-20251001", 50, 10)
        return "done"

    result = run()

    assert result == "done"
    out = capsys.readouterr().out
    assert "[token-spend] worker_a" in out
    assert "2 call(s)" in out
    assert "150 input" in out
    assert "30 output" in out


def test_silent_when_no_usage_recorded(capsys):
    @token_tracked_step("worker_b")
    def run():
        return "no llm calls here"

    run()

    assert capsys.readouterr().out == ""


def test_usage_outside_any_step_is_dropped():
    # No token_tracked_step is open here; record_usage must not raise.
    record_usage("claude-haiku-4-5-20251001", 10, 10)


def test_nested_steps_track_independently(capsys):
    @token_tracked_step("inner")
    def inner():
        record_usage("claude-haiku-4-5-20251001", 5, 5)

    @token_tracked_step("outer")
    def outer():
        record_usage("claude-sonnet-4-6", 1, 1)
        inner()

    outer()

    out = capsys.readouterr().out
    assert "[token-spend] inner: 1 call(s), 5 input / 5 output tokens" in out
    assert "[token-spend] outer: 1 call(s), 1 input / 1 output tokens" in out


def test_record_usage_from_message_reads_usage_object():
    @token_tracked_step("worker_c")
    def run():
        msg = SimpleNamespace(usage=SimpleNamespace(input_tokens=7, output_tokens=3))
        record_usage_from_message("claude-haiku-4-5-20251001", msg)

    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run()
    assert "7 input / 3 output" in buf.getvalue()


def test_record_usage_from_message_tolerates_missing_usage(capsys):
    @token_tracked_step("worker_d")
    def run():
        msg = SimpleNamespace()  # no .usage at all, like minimal test fakes
        record_usage_from_message("claude-haiku-4-5-20251001", msg)

    run()

    out = capsys.readouterr().out
    assert "[token-spend] worker_d: 1 call(s), 0 input / 0 output tokens" in out
