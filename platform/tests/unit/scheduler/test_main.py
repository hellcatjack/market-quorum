from types import SimpleNamespace

from tradingng_platform.config import Settings
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


def test_execution_metadata_forces_stocklean_for_alpha_categories(monkeypatch):
    monkeypatch.setattr(main, "_commit", lambda path: path.name or "root")
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "research-premium-secret")
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://tradingng:test@127.0.0.1/tradingng",
        research_data_vendor_chain=("stocklean",),
    )

    metadata = main._execution_metadata(settings)

    for category in (
        "core_stock_apis",
        "technical_indicators",
        "fundamental_data",
        "news_data",
    ):
        assert metadata.data_vendors[category] == "stocklean"
    assert metadata.data_vendors["macro_data"] == "fred"
    assert metadata.data_vendors["prediction_markets"] == "polymarket"
    assert metadata.vendor_policies == {"stocklean": {"manifest_required": True}}
    assert "research-premium-secret" not in repr(metadata)
