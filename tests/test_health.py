import sys
import time

import pytest

from crash_decoder.cli import main


def test_health_rejects_missing_or_stale_heartbeat(monkeypatch, tmp_path):
    heartbeat = tmp_path / "heartbeat"
    monkeypatch.setattr("crash_decoder.bot.HEALTH", heartbeat)
    monkeypatch.setattr(sys, "argv", ["crash-decoder", "health"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    heartbeat.write_text(str(time.time()))
    main()
    heartbeat.write_text(str(time.time() - 90))
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
