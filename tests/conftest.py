"""Shared fixtures for the DeepSeek Deck test suite.

Everything here is offline: no test may construct a real `openai.AsyncOpenAI`
client or hit the network. Use `make_manager` (backed by `FakeClient` in
fake_openai.py) for anything that needs a SessionManager.

Each test gets its own isolated DECK_HOME (a tmp_path), so tests never read or
write the developer's real ~/.deepseek-deck state and can run in any order /
in parallel.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for `fake_openai`

from deck import config as cfg          # noqa: E402
from deck import folders as _folders    # noqa: E402
from deck.session import SessionManager  # noqa: E402

from fake_openai import FakeClient      # noqa: E402


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Redirect all Deck on-disk state to a throwaway tmp_path for this test."""
    home = tmp_path / "deck_home"
    sessions_dir = home / "sessions"
    monkeypatch.setattr(cfg, "DECK_HOME", home)
    monkeypatch.setattr(cfg, "SESSIONS_DIR", sessions_dir)
    monkeypatch.setattr(cfg, "DAEMON_FILE", home / "daemon.json")
    monkeypatch.setattr(cfg, "LOG_FILE", home / "daemon.log")
    monkeypatch.setattr(_folders, "FOLDERS_FILE", home / "folders.json")
    cfg.init_dirs()
    return home


def _fake_config(max_turns: int = 5) -> cfg.DeckConfig:
    return cfg.DeckConfig(
        api_key="test-key-not-real",
        model="fake-model",
        max_turns=max_turns,
        allowed_tools=["Read"],
        base_url="http://fake.invalid",
        port=0,
    )


@pytest.fixture
def make_manager(isolated_home):
    """Factory: make_manager(script=[...], blocking=False, max_turns=5)
    -> (SessionManager, FakeClient). The manager uses a fake OpenAI client and
    an isolated on-disk home, so it's fully offline and safe to run anywhere.
    """
    def _make(script=None, blocking: bool = False, max_turns: int = 5,
              config: cfg.DeckConfig | None = None):
        client = FakeClient(script=script, blocking=blocking)
        mgr = SessionManager(config or _fake_config(max_turns=max_turns), client=client)
        return mgr, client
    return _make
