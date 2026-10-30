import sys

import ars3_autostart


def test_autostart_launches_bot_and_dashboard_with_the_running_interpreter(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, target, args):
            started.append(args)

        def start(self):
            pass

    monkeypatch.setattr(ars3_autostart.threading, "Thread", FakeThread)
    monkeypatch.setattr(ars3_autostart.time, "sleep", lambda s: None)
    ars3_autostart.run_full_stack()
    (bot, _), (dashboard, _) = started
    assert bot == [sys.executable, ars3_autostart.BOT_SCRIPT]
    assert dashboard[:4] == [sys.executable, "-m", "streamlit", "run"]
