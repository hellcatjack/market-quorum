import pytest

from tradingng_platform.auth.principal import Principal


def test_principal_accepts_required_scope():
    principal = Principal("issuer", "alice", "user", frozenset({"assessments:submit"}))

    principal.require("assessments:submit")


def test_principal_rejects_missing_scope():
    principal = Principal("issuer", "alice", "user", frozenset())

    with pytest.raises(PermissionError, match="assessments:read"):
        principal.require("assessments:read")
