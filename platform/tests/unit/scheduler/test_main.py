from types import SimpleNamespace

from tradingng_platform.scheduler import main


def test_commit_fingerprint_marks_tracked_worktree_changes(monkeypatch, tmp_path):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        if command[-2:] == ["rev-parse", "HEAD"]:
            return SimpleNamespace(stdout="abc123\n")
        return SimpleNamespace(stdout=" M platform/source.py\n")

    monkeypatch.setattr(main.subprocess, "run", run)

    assert main._commit(tmp_path) == "abc123-dirty"
    assert commands == [
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        [
            "git",
            "-C",
            str(tmp_path),
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
    ]
