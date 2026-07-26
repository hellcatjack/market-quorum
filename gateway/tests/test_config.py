import pytest

from codex_gateway.config import Settings, parse_codex_version


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("CODEX_GATEWAY_PORT", raising=False)
    monkeypatch.delenv("CODEX_GATEWAY_REQUEST_TIMEOUT_SECONDS", raising=False)
    settings = Settings.from_env()
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.request_timeout_seconds is None
    assert settings.max_body_bytes == 2 * 1024 * 1024
    assert settings.codex_bin == "codex"


@pytest.mark.parametrize(
    "name,value",
    [
        ("CODEX_GATEWAY_PORT", "0"),
        ("CODEX_GATEWAY_PORT", "65536"),
        ("CODEX_GATEWAY_REQUEST_TIMEOUT_SECONDS", "-1"),
        ("CODEX_GATEWAY_REQUEST_TIMEOUT_SECONDS", "abc"),
    ],
)
def test_invalid_settings_fail(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        Settings.from_env()


def test_zero_request_timeout_disables_deadline(monkeypatch):
    monkeypatch.setenv("CODEX_GATEWAY_REQUEST_TIMEOUT_SECONDS", "0")
    assert Settings.from_env().request_timeout_seconds is None


def test_positive_request_timeout_is_preserved(monkeypatch):
    monkeypatch.setenv("CODEX_GATEWAY_REQUEST_TIMEOUT_SECONDS", "21600")
    assert Settings.from_env().request_timeout_seconds == 21600


def test_parse_codex_version():
    assert parse_codex_version("codex-cli 0.145.0") == (0, 145, 0)


def test_parse_codex_version_rejects_unknown_output():
    with pytest.raises(ValueError, match="Unable to parse"):
        parse_codex_version("custom build")
