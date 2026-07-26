import ast
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_shell_scripts_are_valid_bash():
    for script in ("scripts/bootstrap.sh", "scripts/run_gateway.sh"):
        path = ROOT / script
        result = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True,
            check=False,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert os.access(path, os.X_OK)


def test_bootstrap_applies_dependency_constraints():
    bootstrap = (ROOT / "scripts/bootstrap.sh").read_text(encoding="utf-8")
    assert bootstrap.count('"$project_root/constraints.txt"') == 3


def test_smoke_script_makes_one_http_request():
    source = (ROOT / "scripts/smoke_gateway.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    requests = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "httpx"
    ]
    assert len(requests) == 1
    assert requests[0].func.attr == "post"


def test_readmes_are_bilingual_and_link_to_each_other():
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    assert english.startswith("# MarketQuorum")
    assert chinese.startswith("# MarketQuorum")
    assert "[简体中文](README.zh-CN.md)" in english
    assert "[English](README.md)" in chinese


def test_readmes_document_required_configuration_and_current_capacity():
    for filename in ("README.md", "README.zh-CN.md"):
        readme = (ROOT / filename).read_text(encoding="utf-8")
        for value in (
            "TRADINGAGENTS_LLM_PROVIDER=openai_compatible",
            "TRADINGAGENTS_DEEP_THINK_LLM=codex",
            "TRADINGAGENTS_QUICK_THINK_LLM=codex",
            "TRADINGAGENTS_LLM_BACKEND_URL=http://127.0.0.1:8000/v1",
            "codex login status",
            "mcp_servers.playwright.enabled=false",
            "networkAccess",
            "scripts/smoke_gateway.py",
            "32",
        ):
            assert value in readme
        assert "non-configurable hard ceiling of three" not in readme


def test_publication_docs_define_privacy_and_contribution_boundaries():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for value in ("reports/", "*.log", "*.pem", "*.key", "*.sqlite3"):
        assert value in ignore

    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert "GitHub Security Advisory" in security
    assert "reports/" in contributing
    assert "TauricResearch/TradingAgents" in notices
    assert "Apache License 2.0" in notices


def test_example_env_has_only_local_placeholder_key():
    env = (ROOT / ".env.tradingagents.example").read_text(encoding="utf-8")
    assert "OPENAI_COMPATIBLE_API_KEY=local" in env
    assert "OPENAI_API_KEY=" not in env
