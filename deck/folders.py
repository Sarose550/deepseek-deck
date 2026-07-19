"""Folders — the top-level grouping in the Deck (your 'conversations').

A folder groups several agent panels and carries the project directory those
agents are mounted at. Folders come from the DAG harness (auto-named) or are
created by hand in the UI. 'Unfiled' is a special always-present folder with no
project directory (its agents get isolated scratch dirs).

Isolation:
  - 'shared'   : every agent works directly in the folder's directory.
  - 'worktree' : each agent gets its own `git worktree` (own branch) under
                 <dir>/.deck-worktrees/<agent>; falls back to 'shared' if the
                 directory isn't a git repo.
"""
from __future__ import annotations

import json
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from . import config as cfg

FOLDERS_FILE = cfg.DECK_HOME / "folders.json"
UNFILED_ID = "unfiled"


@dataclass
class Folder:
    id: str
    name: str
    workspace: Optional[str] = None          # abs path, or None (scratch)
    isolation: str = "shared"                 # 'shared' | 'worktree'
    archived: bool = False
    source: str = "manual"                    # 'manual' | 'dag' | 'unfiled'
    created_at: float = field(default_factory=time.time)
    last_interacted_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        """Update last_interacted_at to now (called when agents are created or conversed with)."""
        self.last_interacted_at = time.time()

    def to_dict(self) -> dict:
        return asdict(self)


class FolderStore:
    def __init__(self):
        self.folders: dict[str, Folder] = {}
        self._load()
        if UNFILED_ID not in self.folders:
            self.folders[UNFILED_ID] = Folder(
                id=UNFILED_ID, name="Unfiled", workspace=None,
                isolation="shared", source="unfiled")
            self._save()

    # --- persistence ------------------------------------------------------

    def _load(self) -> None:
        if not FOLDERS_FILE.exists():
            return
        try:
            data = json.loads(FOLDERS_FILE.read_text(encoding="utf-8"))
            for d in data:
                self.folders[d["id"]] = Folder(**d)
        except (json.JSONDecodeError, OSError, TypeError):
            pass

    def _save(self) -> None:
        try:
            FOLDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
            FOLDERS_FILE.write_text(
                json.dumps([f.to_dict() for f in self.folders.values()],
                           ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    # --- crud -------------------------------------------------------------

    def create(self, name: str, workspace: Optional[str] = None,
               isolation: str = "shared", source: str = "manual") -> Folder:
        fid = uuid.uuid4().hex[:6]
        ws = None
        if workspace:
            p = Path(workspace).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            ws = str(p)
        f = Folder(id=fid, name=name or fid, workspace=ws,
                   isolation=isolation if isolation in ("shared", "worktree") else "shared",
                   source=source)
        self.folders[fid] = f
        self._save()
        return f

    def get(self, key: str) -> Optional[Folder]:
        if key in self.folders:
            return self.folders[key]
        for f in self.folders.values():           # address by name too
            if f.name == key:
                return f
        return None

    def get_or_create(self, key: str, workspace: Optional[str] = None,
                      isolation: str = "shared", source: str = "manual") -> Folder:
        f = self.get(key)
        if f:
            return f
        return self.create(key, workspace, isolation, source)

    def rename(self, fid: str, name: str) -> bool:
        f = self.folders.get(fid)
        if not f or f.id == UNFILED_ID:
            return False
        f.name = name
        self._save()
        return True

    def set_archived(self, fid: str, archived: bool) -> bool:
        f = self.folders.get(fid)
        if not f or f.id == UNFILED_ID:
            return False
        f.archived = archived
        self._save()
        return True

    def delete(self, fid: str) -> bool:
        if fid == UNFILED_ID or fid not in self.folders:
            return False
        self.folders.pop(fid, None)
        self._save()
        return True

    def touch(self, fid: str) -> None:
        """Mark a folder as recently interacted with."""
        f = self.folders.get(fid)
        if f:
            f.touch()
            self._save()

    def list(self) -> list[dict]:
        return [f.to_dict() for f in sorted(self.folders.values(),
                                            key=lambda f: (f.id != UNFILED_ID, -f.last_interacted_at))]


# --- worktree helpers ------------------------------------------------------

def is_git_repo(path: Path) -> bool:
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, timeout=10)
        return r.returncode == 0 and r.stdout.strip() == "true"
    except (subprocess.SubprocessError, OSError):
        return False


def make_worktree(repo: Path, agent_id: str) -> Optional[Path]:
    """Create a git worktree for an agent. Returns its path, or None on failure."""
    wt = repo / ".deck-worktrees" / agent_id
    branch = f"deck/{agent_id}"
    try:
        wt.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", branch, str(wt), "HEAD"],
            capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return wt
    except (subprocess.SubprocessError, OSError):
        pass
    return None


def remove_worktree(repo: Path, wt: Path) -> None:
    try:
        subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)],
                       capture_output=True, text=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        pass
