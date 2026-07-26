from pathlib import Path

ROOT = Path(__file__).parents[1]


def _assignments(path: Path) -> dict[str, str]:
    return {
        key.strip(): value.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
        for key, value in [line.split("=", 1)]
    }


def test_gateway_user_service_is_loopback_project_service():
    unit = (ROOT / "systemd/user/tradingng-codex-gateway.service").read_text()
    assert "WorkingDirectory=/app/devs/TradingNG" in unit
    assert "ExecStart=/app/devs/TradingNG/.venv/bin/python -m codex_gateway" in unit
    assert "/home/hellcat/.nvm/versions/node/v22.21.1/bin" in unit
    assert "Restart=always" in unit
    assert "WantedBy=default.target" in unit
    assert "codex-gateway-audit" not in unit
    assert "18001" not in unit


def test_tradingagents_example_connects_directly_to_gateway():
    values = _assignments(ROOT / ".env.tradingagents.example")
    assert values["TRADINGAGENTS_LLM_PROVIDER"] == "openai_compatible"
    assert values["TRADINGAGENTS_DEEP_THINK_LLM"] == "codex"
    assert values["TRADINGAGENTS_QUICK_THINK_LLM"] == "codex"
    assert values["TRADINGAGENTS_LLM_BACKEND_URL"] == "http://127.0.0.1:8000/v1"
    assert "18001" not in values["TRADINGAGENTS_LLM_BACKEND_URL"]


def test_active_dotenv_is_git_ignored():
    patterns = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in patterns


def test_validation_provider_template_is_safe_by_default():
    values = _assignments(ROOT / ".env.platform.example")
    assert values["TRADINGNG_VALIDATION_PRICE_PROVIDERS"] == "yfinance"
    assert values["TRADINGNG_ALPHA_VANTAGE_API_KEY"] == ""
    assert values["TRADINGNG_ALPHA_VANTAGE_REQUESTS_PER_MINUTE"] == "75"


def test_offline_verification_checks_validation_worker_service():
    script = (ROOT / "scripts/verify_platform.sh").read_text(encoding="utf-8")
    assert "is-active --quiet tradingng-platform-validation.service" in script
