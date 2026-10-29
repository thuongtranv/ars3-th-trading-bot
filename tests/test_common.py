import requests

from common import notify
from common.env import get_env
from common.retry import retry


def test_get_env_prefers_first_non_empty_name(monkeypatch):
    monkeypatch.setenv("NEW_NAME", "")
    monkeypatch.setenv("OLD_NAME", "legacy")
    assert get_env("NEW_NAME", "OLD_NAME") == "legacy"
    assert get_env("NEW_NAME", "MISSING", default="x") == "x"


def test_retry_backs_off_then_reraises(monkeypatch):
    sleeps, calls = [], []
    monkeypatch.setattr("common.retry.time.sleep", sleeps.append)

    @retry(times=3, base_delay=1.0, on=(ValueError,))
    def flaky():
        calls.append(1)
        raise ValueError("boom")

    try:
        flaky()
    except ValueError:
        pass
    assert len(calls) == 3 and sleeps == [1.0, 2.0]


def test_retry_ignores_other_exceptions():
    @retry(times=3, on=(ValueError,))
    def bad():
        raise KeyError("x")

    try:
        bad()
    except KeyError:
        return
    raise AssertionError("KeyError should propagate immediately")


def test_telegram_without_credentials_returns_false_and_does_not_raise():
    assert notify.send_telegram("hi") is False


def test_telegram_adds_prefix_and_reports_http_errors(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    monkeypatch.setattr(notify.time, "sleep", lambda s: None)
    sent = []

    class Resp:
        status_code, text = 200, ""

    monkeypatch.setattr(notify.requests, "post", lambda url, data, files, timeout: sent.append(data) or Resp())
    assert notify.send_telegram("body", prefix="[X] ") is True
    assert sent[0]["text"] == "[X] body" and sent[0]["chat_id"] == "c"

    def down(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(notify.requests, "post", down)
    assert notify.send_telegram("body") is False
