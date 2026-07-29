import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/sync_keycloak_user_management.py"


def _module():
    specification = importlib.util.spec_from_file_location(
        "sync_keycloak_user_management",
        SCRIPT,
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def snapshot(*, converged=False, enabled_admin=True):
    roles = {"Admin", "User"} if converged else {"Admin", "Analyst", "Viewer"}
    scopes = {"assessments:review", "users:manage"} if converged else set()
    clients = {"tradingng-user-admin"} if converged else set()
    web_scopes = {"assessments:review", "users:manage"} if converged else set()
    users = [
        {
            "id": "admin-sub",
            "username": "admin",
            "enabled": enabled_admin,
            "roles": {"Admin"},
        },
        {
            "id": "analyst-sub",
            "username": "analyst",
            "enabled": True,
            "roles": {"User"} if converged else {"Analyst"},
        },
        {
            "id": "viewer-sub",
            "username": "viewer",
            "enabled": False,
            "roles": {"User"} if converged else {"Viewer"},
        },
    ]
    return {
        "roles": roles,
        "scopes": scopes,
        "clients": clients,
        "web_scopes": web_scopes,
        "service_roles": (
            {"query-users", "view-users", "manage-users"} if converged else set()
        ),
        "users": users,
    }


def test_plan_creates_missing_identity_resources_and_migrates_legacy_users():
    module = _module()

    actions = module.plan(snapshot())

    assert actions == (
        "create_role:User",
        "create_scope:assessments:review",
        "create_scope:users:manage",
        "create_client:tradingng-user-admin",
        "grant_service_roles:tradingng-user-admin",
        "attach_web_scope:assessments:review",
        "attach_web_scope:users:manage",
        "migrate_user:analyst-sub:User",
        "logout_user:analyst-sub",
        "migrate_user:viewer-sub:User",
        "logout_user:viewer-sub",
        "remove_role:Analyst",
        "remove_role:Viewer",
    )


def test_plan_is_empty_after_convergence():
    module = _module()

    assert module.plan(snapshot(converged=True)) == ()


def test_plan_refuses_to_mutate_without_an_enabled_admin():
    module = _module()

    with pytest.raises(module.KeycloakUserManagementError, match="enabled Admin"):
        module.plan(snapshot(enabled_admin=False))


def test_admin_with_legacy_role_keeps_admin_and_only_drops_legacy_assignment():
    module = _module()
    state = snapshot()
    state["users"][0]["roles"].add("Viewer")

    actions = module.plan(state)

    assert "migrate_user:admin-sub:Admin" in actions
    assert "logout_user:admin-sub" in actions
    assert "migrate_user:admin-sub:User" not in actions


def test_report_never_contains_client_secret():
    module = _module()
    secret = "private-management-secret"

    report = module.render_report(module.plan(snapshot()), secret=secret)

    assert secret not in report
    assert "secret" not in report.casefold()


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("create_scope:assessments:review", ("create_scope", "assessments:review", None)),
        ("attach_web_scope:users:manage", ("attach_web_scope", "users:manage", None)),
        ("migrate_user:user-sub:User", ("migrate_user", "user-sub", "User")),
    ],
)
def test_action_parser_preserves_colons_inside_scope_names(action, expected):
    module = _module()

    assert module.parse_action(action) == expected
