"""A folder with isolation='worktree' mounted on a real git repo must give
each agent its own `git worktree` on branch `deck/<id>`, rooted at
<repo>/.deck-worktrees/<id>."""
from __future__ import annotations

import subprocess

from fake_openai import text_turn


def _run(*args, cwd):
    r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"{args} failed: {r.stderr}"
    return r.stdout


def _init_git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _run("git", "init", "-q", cwd=path)
    _run("git", "config", "user.email", "test@example.com", cwd=path)
    _run("git", "config", "user.name", "Test", cwd=path)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _run("git", "add", "README.md", cwd=path)
    _run("git", "commit", "-q", "-m", "initial commit", cwd=path)


async def test_agent_gets_its_own_worktree_and_branch(make_manager, tmp_path):
    repo = tmp_path / "project"
    _init_git_repo(repo)

    mgr, client = make_manager(script=[text_turn("noop")])
    folder = mgr.folder_create(name="WT", workspace=str(repo), isolation="worktree")
    fid = folder["id"]

    s = mgr.create(task="do something benign", folder=fid)
    await s._task  # let the (harmless) scripted turn finish

    expected_ws = repo / ".deck-worktrees" / s.id
    assert s.workspace == expected_ws
    assert s.worktree_repo == str(repo)
    assert expected_ws.is_dir()

    listing = _run("git", "worktree", "list", cwd=repo)
    assert str(expected_ws) in listing

    branch_out = _run("git", "-C", str(expected_ws), "branch", "--show-current", cwd=repo).strip()
    assert branch_out == f"deck/{s.id}"


def test_resolve_workspace_directly_for_worktree_folder(make_manager, tmp_path):
    """Lower-level check on the resolver itself, independent of the agent loop."""
    from deck import folders as _folders

    repo = tmp_path / "project2"
    _init_git_repo(repo)

    mgr, client = make_manager()
    folder = _folders.Folder(id="f1", name="WT2", workspace=str(repo), isolation="worktree")

    ws, worktree_repo = mgr._resolve_workspace(folder, "abc123", None)

    assert ws == repo / ".deck-worktrees" / "abc123"
    assert worktree_repo == str(repo)
    listing = _run("git", "worktree", "list", cwd=repo)
    assert str(ws) in listing
