from pathlib import Path

import pytest
from mysql_database import validate_database_name, validate_test_database_name

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("name", ["tradingng_test_abc123", "tradingNG"])
def test_database_name_validation(name):
    assert validate_database_name(name) == name


@pytest.mark.parametrize(
    "name",
    ["test", "tradingng_test_", "../escape", "x;drop", "tradingNG"],
)
def test_test_database_cleanup_rejects_unsafe_names(name):
    with pytest.raises(ValueError, match="test database name"):
        validate_test_database_name(name)


def test_test_database_name_accepts_a_bounded_random_suffix():
    assert validate_test_database_name("tradingng_test_0123456789ab") == (
        "tradingng_test_0123456789ab"
    )


def test_platform_verifier_uses_a_random_mysql_database_and_exact_cleanup():
    source = (ROOT / "scripts" / "verify_platform.sh").read_text(encoding="utf-8")

    assert 'mysql_test_name="tradingng_test_$(openssl rand -hex 6)"' in source
    assert "scripts/mysql_database.py" in source
    assert '--env-file "$mysql_env_file"' in source
    assert 'create-test --name "$mysql_test_name"' in source
    assert 'drop-test --name "$mysql_test_name" --confirm-drop "$mysql_test_name"' in source
