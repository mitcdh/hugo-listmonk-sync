from __future__ import annotations

from hugo_listmonk_sync.main import main


def test_invalid_startup_configuration_exits_nonzero(monkeypatch):
    monkeypatch.setattr(
        "hugo_listmonk_sync.main.os.environ",
        {},
    )

    assert main() == 2
