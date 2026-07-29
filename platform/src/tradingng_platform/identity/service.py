import secrets
from typing import Literal
from uuid import UUID

from pydantic import SecretStr

from tradingng_platform.auth.principal import Principal
from tradingng_platform.identity.contracts import (
    CreateUserCommand,
    KeycloakUser,
    LocalIdentity,
    SessionSummary,
    TemporaryCredential,
    UpdateUserCommand,
    UserActionFlags,
    UserDetailView,
    UserPage,
    UserView,
)
from tradingng_platform.identity.errors import IdentityError, identity_error


class IdentityAdminService:
    def __init__(self, keycloak, repository, issuer: str):
        self.keycloak = keycloak
        self.repository = repository
        self.issuer = issuer

    @staticmethod
    def _require_admin(principal: Principal) -> None:
        if "Admin" not in principal.roles or "users:manage" not in principal.scopes:
            raise PermissionError("Admin role and users:manage scope are required")

    async def list_users(
        self,
        principal: Principal,
        *,
        search: str | None,
        role: Literal["Admin", "User"] | None,
        status: Literal["active", "disabled"] | None,
        page: int,
        page_size: int,
    ) -> UserPage:
        self._require_admin(principal)
        authoritative = await self.keycloak.list_users(
            search=search,
            first=0,
            maximum=1000,
        )
        users = [
            user
            for user in authoritative.items
            if (role is None or user.role == role)
            and (
                status is None
                or (status == "active" and user.enabled)
                or (status == "disabled" and not user.enabled)
            )
        ]
        start = (page - 1) * page_size
        selected = users[start : start + page_size]
        views = []
        async with self.repository.transaction() as transaction:
            for user in selected:
                sync = await transaction.sync_authoritative(user, self.issuer)
                views.append(self._view(sync.identity, user))
        return UserPage(items=tuple(views), page=page, page_size=page_size, total=len(users))

    async def get_user(self, principal: Principal, user_id: UUID) -> UserDetailView:
        self._require_admin(principal)
        async with self.repository.transaction() as transaction:
            local = await transaction.get_by_id(user_id)
            if local is None:
                raise identity_error("user_not_found")
            authoritative = await self.keycloak.get_user(local.subject)
            sync = await transaction.sync_authoritative(authoritative, self.issuer)
            enabled_admins = await transaction.enabled_admin_count()
        return await self._detail(principal, sync.identity, authoritative, enabled_admins)

    async def create_user(
        self,
        principal: Principal,
        command: CreateUserCommand,
        request_id: str,
    ) -> TemporaryCredential:
        self._require_admin(principal)
        await self._assert_unique(command.username, str(command.email))
        password = SecretStr(secrets.token_urlsafe(32))
        subject = await self.keycloak.create_user(
            username=command.username,
            display_name=command.display_name,
            email=str(command.email),
            enabled=False,
        )
        await self.keycloak.replace_role(subject, command.role)
        await self.keycloak.set_temporary_password(subject, password.get_secret_value())
        await self.keycloak.update_user(
            subject,
            display_name=command.display_name,
            email=str(command.email),
            enabled=True,
        )
        await self.keycloak.logout(subject)
        authoritative = await self.keycloak.get_user(subject)
        try:
            async with self.repository.transaction(guard=True) as transaction:
                sync = await transaction.sync_authoritative(authoritative, self.issuer)
                await transaction.append_audit(
                    principal,
                    "user.create",
                    sync.identity,
                    request_id,
                    {
                        "changed_fields": ["username", "display_name", "email", "role", "status"],
                        "new_role": command.role,
                        "new_status": "active",
                        "keycloak_subject": subject,
                        "result": "succeeded",
                    },
                )
        except IdentityError:
            raise
        except Exception:
            raise identity_error("identity_sync_pending") from None
        return TemporaryCredential(self._view(sync.identity, authoritative), password)

    async def update_user(
        self,
        principal: Principal,
        user_id: UUID,
        command: UpdateUserCommand,
        request_id: str,
    ) -> UserDetailView:
        self._require_admin(principal)
        try:
            async with self.repository.transaction(guard=True) as transaction:
                local = await transaction.get_by_id(user_id)
                if local is None:
                    raise identity_error("user_not_found")
                current = await self.keycloak.get_user(local.subject)
                await self._protect_admin_change(
                    principal,
                    local,
                    command,
                    await transaction.enabled_admin_count(),
                )
                display_name = command.display_name or current.display_name
                email = str(command.email) if command.email is not None else current.email
                if email is None:
                    email = ""
                enabled = command.enabled if command.enabled is not None else current.enabled
                profile_changed = (
                    display_name != current.display_name or email != (current.email or "")
                )
                status_changed = enabled != current.enabled
                role_changed = command.role is not None and command.role != current.role
                if profile_changed or status_changed:
                    await self.keycloak.update_user(
                        local.subject,
                        display_name=display_name,
                        email=email,
                        enabled=enabled,
                    )
                if role_changed:
                    await self.keycloak.replace_role(local.subject, command.role)
                if role_changed or status_changed:
                    await self.keycloak.logout(local.subject)
                authoritative = await self.keycloak.get_user(local.subject)
                sync = await transaction.sync_authoritative(authoritative, self.issuer)
                if profile_changed:
                    await self._audit(
                        transaction,
                        principal,
                        "user.profile_update",
                        sync.identity,
                        request_id,
                        ["display_name", "email"],
                    )
                if role_changed:
                    await transaction.append_audit(
                        principal,
                        "user.role_change",
                        sync.identity,
                        request_id,
                        {
                            "changed_fields": ["role"],
                            "old_role": current.role,
                            "new_role": authoritative.role,
                            "keycloak_subject": local.subject,
                            "result": "succeeded",
                        },
                    )
                if status_changed:
                    await transaction.append_audit(
                        principal,
                        "user.enable" if enabled else "user.disable",
                        sync.identity,
                        request_id,
                        {
                            "changed_fields": ["status"],
                            "old_status": "active" if current.enabled else "disabled",
                            "new_status": "active" if enabled else "disabled",
                            "keycloak_subject": local.subject,
                            "result": "succeeded",
                        },
                    )
                enabled_admins = await transaction.enabled_admin_count()
        except IdentityError:
            raise
        except Exception:
            raise identity_error("identity_sync_pending") from None
        return await self._detail(principal, sync.identity, authoritative, enabled_admins)

    async def reset_password(
        self,
        principal: Principal,
        user_id: UUID,
        request_id: str,
    ) -> TemporaryCredential:
        self._require_admin(principal)
        password = SecretStr(secrets.token_urlsafe(32))
        try:
            async with self.repository.transaction(guard=True) as transaction:
                local = await transaction.get_by_id(user_id)
                if local is None:
                    raise identity_error("user_not_found")
                await self.keycloak.set_temporary_password(
                    local.subject,
                    password.get_secret_value(),
                )
                await self.keycloak.logout(local.subject)
                authoritative = await self.keycloak.get_user(local.subject)
                sync = await transaction.sync_authoritative(authoritative, self.issuer)
                await transaction.append_audit(
                    principal,
                    "user.password_reset",
                    sync.identity,
                    request_id,
                    {
                        "changed_fields": ["password"],
                        "keycloak_subject": local.subject,
                        "result": "succeeded",
                    },
                )
        except IdentityError:
            raise
        except Exception:
            raise identity_error("identity_sync_pending") from None
        return TemporaryCredential(self._view(sync.identity, authoritative), password)

    async def logout_user(
        self,
        principal: Principal,
        user_id: UUID,
        request_id: str,
    ) -> UserDetailView:
        self._require_admin(principal)
        async with self.repository.transaction(guard=True) as transaction:
            local = await transaction.get_by_id(user_id)
            if local is None:
                raise identity_error("user_not_found")
            await self.keycloak.logout(local.subject)
            authoritative = await self.keycloak.get_user(local.subject)
            sync = await transaction.sync_authoritative(authoritative, self.issuer)
            await transaction.append_audit(
                principal,
                "user.logout",
                sync.identity,
                request_id,
                {
                    "changed_fields": ["sessions"],
                    "keycloak_subject": local.subject,
                    "result": "succeeded",
                },
            )
            enabled_admins = await transaction.enabled_admin_count()
        return await self._detail(principal, sync.identity, authoritative, enabled_admins)

    async def _assert_unique(self, username: str, email: str) -> None:
        by_username = await self.keycloak.list_users(search=username, first=0, maximum=20)
        if any(user.username.casefold() == username.casefold() for user in by_username.items):
            raise identity_error("username_conflict")
        by_email = await self.keycloak.list_users(search=email, first=0, maximum=20)
        if any((user.email or "").casefold() == email.casefold() for user in by_email.items):
            raise identity_error("email_conflict")

    @staticmethod
    async def _protect_admin_change(
        principal: Principal,
        local: LocalIdentity,
        command: UpdateUserCommand,
        enabled_admin_count: int,
    ) -> None:
        removes_admin = local.role == "Admin" and command.role == "User"
        disables_admin = (
            local.role == "Admin" and local.status == "active" and command.enabled is False
        )
        if principal.subject == local.subject and (removes_admin or disables_admin):
            raise identity_error("self_admin_change_forbidden")
        if enabled_admin_count <= 1 and (removes_admin or disables_admin):
            raise identity_error("last_admin_protected")

    async def _detail(
        self,
        principal: Principal,
        local: LocalIdentity,
        authoritative: KeycloakUser,
        enabled_admin_count: int,
    ) -> UserDetailView:
        sessions = await self.keycloak.sessions(local.subject)
        self_protected = principal.subject == local.subject and local.role == "Admin"
        last_protected = (
            local.role == "Admin" and local.status == "active" and enabled_admin_count <= 1
        )
        reasons = {}
        if self_protected:
            reasons.update(
                {
                    "change_role": "self_admin_change_forbidden",
                    "change_enabled": "self_admin_change_forbidden",
                }
            )
        elif last_protected:
            reasons.update(
                {
                    "change_role": "last_admin_protected",
                    "change_enabled": "last_admin_protected",
                }
            )
        last_access = max((session.last_access_at for session in sessions), default=None)
        return UserDetailView(
            user=self._view(local, authoritative),
            sessions=SessionSummary(active_count=len(sessions), last_access_at=last_access),
            allowed_actions=UserActionFlags(
                edit_profile=True,
                change_role=not (self_protected or last_protected),
                change_enabled=not (self_protected or last_protected),
                reset_password=True,
                logout=True,
            ),
            action_reasons=reasons,
        )

    @staticmethod
    async def _audit(
        transaction,
        principal,
        action,
        target,
        request_id,
        changed_fields,
    ):
        await transaction.append_audit(
            principal,
            action,
            target,
            request_id,
            {
                "changed_fields": changed_fields,
                "keycloak_subject": target.subject,
                "result": "succeeded",
            },
        )

    @staticmethod
    def _view(local: LocalIdentity, authoritative: KeycloakUser) -> UserView:
        if local.role not in {"Admin", "User"}:
            raise identity_error("identity_role_invalid")
        return UserView(
            id=local.id,
            subject=local.subject,
            username=authoritative.username,
            display_name=authoritative.display_name,
            email=authoritative.email,
            role=local.role,
            enabled=local.status == "active",
            synced_at=local.synced_at,
        )


class UnavailableIdentityAdminService:
    @staticmethod
    async def _raise(*args, **kwargs):
        raise identity_error("identity_provider_forbidden")

    list_users = _raise
    get_user = _raise
    create_user = _raise
    update_user = _raise
    reset_password = _raise
    logout_user = _raise
