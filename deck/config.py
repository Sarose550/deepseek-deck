"""DeepSeek Deck configuration.

Reuses the DeepSeek API key + model settings from ~/.deepseek-mcp/config.json
(so the Deck and the deepseek-mcp server share one source of truth), and adds
Deck-specific settings: where state lives, the daemon port, and where the
sibling deepseek-as-subagent package (tool implementations) can be found.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# --- Locations -------------------------------------------------------------

DECK_HOME = Path(os.getenv("DECK_HOME", str(Path.home() / ".deepseek-deck")))
SESSIONS_DIR = DECK_HOME / "sessions"
DAEMON_FILE = DECK_HOME / "daemon.json"     # {pid, port, started_at}
LOCK_FILE = DECK_HOME / "daemon.lock"       # serializes ensure_daemon() across processes
LOG_FILE = DECK_HOME / "daemon.log"

DEEPSEEK_MCP_CONFIG = Path.home() / ".deepseek-mcp" / "config.json"

# The sibling package that owns the tool implementations (Read/Write/Bash/...).
# Stdlib-only modules, imported by path so we don't vendor a second copy.
DEEPSEEK_SUBAGENT_SRC = Path(
    os.getenv(
        "DEEPSEEK_SUBAGENT_SRC",
        str(Path.home() / "Documents" / "deepseek-as-subagent" / "src"),
    )
)

DEFAULT_PORT = int(os.getenv("DECK_PORT", "8787"))
DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_MAX_TURNS = 50
DEFAULT_ALLOWED_TOOLS = ["Read", "Write", "Edit", "Bash", "Glob", "Grep", "NotebookEdit"]
DEFAULT_BASE_URL = "https://api.deepseek.com"

# Max concurrent DeepSeek workers actively calling the API. Extra spawns queue.
MAX_CONCURRENCY = int(os.getenv("DECK_MAX_CONCURRENCY", "12"))


def ensure_tools_importable() -> None:
    """Put the deepseek-as-subagent src on sys.path so `deepseek_mcp` imports.

    Raises a clear error if the sibling repo is missing, since the whole Deck
    depends on its tool implementations.
    """
    src = DEEPSEEK_SUBAGENT_SRC
    if not (src / "deepseek_mcp" / "tools.py").exists():
        raise RuntimeError(
            f"Cannot find deepseek_mcp tool implementations under {src}. "
            f"Set DEEPSEEK_SUBAGENT_SRC to the 'src' dir of the "
            f"deepseek-as-subagent checkout."
        )
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


@dataclass
class DeckConfig:
    api_key: str
    model: str = DEFAULT_MODEL
    max_turns: int = DEFAULT_MAX_TURNS
    allowed_tools: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_TOOLS))
    base_url: str = DEFAULT_BASE_URL
    port: int = DEFAULT_PORT

    @classmethod
    def load(cls) -> "DeckConfig":
        data: dict = {}
        if DEEPSEEK_MCP_CONFIG.exists():
            try:
                data = json.loads(DEEPSEEK_MCP_CONFIG.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}

        api_key = (os.getenv("DEEPSEEK_API_KEY") or data.get("api_key", "")).strip()
        if not api_key or api_key == "PASTE_YOUR_DEEPSEEK_KEY_HERE":
            raise RuntimeError(
                "DeepSeek API key not configured. Set DEEPSEEK_API_KEY or edit "
                f"{DEEPSEEK_MCP_CONFIG}"
            )

        allowed = data.get("allowed_tools", list(DEFAULT_ALLOWED_TOOLS))
        if not isinstance(allowed, list) or not all(isinstance(t, str) for t in allowed):
            allowed = list(DEFAULT_ALLOWED_TOOLS)

        try:
            max_turns = max(1, int(data.get("max_turns", DEFAULT_MAX_TURNS)))
        except (TypeError, ValueError):
            max_turns = DEFAULT_MAX_TURNS

        return cls(
            api_key=api_key,
            model=data.get("model", DEFAULT_MODEL),
            max_turns=max_turns,
            allowed_tools=allowed,
            base_url=data.get("base_url", DEFAULT_BASE_URL),
            port=DEFAULT_PORT,
        )


def init_dirs() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
