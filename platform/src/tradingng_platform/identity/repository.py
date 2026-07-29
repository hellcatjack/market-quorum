from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, delete, func, select

from tradingng_platform.auth.oidc import FORMAL_ROLES
from tradingng_platform.auth.principal import Principal
from tradingng_platform.identity.contracts import IdentitySync, KeycloakUser, LocalIdentity
from tradingng_platform.models import AuditEvent, Role, User, UserRole
from tradingng_platform.persistence.locks import acquire_transaction_lock
from tradingng_platform.persistence.upsert import insert_ignore, session_dialect


class IdentityRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    @asynccontextmanager
    async def transaction(self, *, guard: bool = False):
        async with self.sessions() as session, session.begin():
            if guard:
                await acquire_transaction_lock(session, "identity-admin-guard")
            yield IdentityTransaction(session)

    async def get_human(
        self,
        issuer: str,
        subject: str,
        *,
        for_update: bool = False,
    ) -> LocalIdentity | None:
        async with self.sessions() as session:
            statement = (
                select(User, Role.name)
                .outerjoin(UserRole, UserRole.user_id == User.id)
                .outerjoin(Role, Role.id == UserRole.role_id)
                .where(User.issuer == issuer, User.subject == subject)
            )
            if for_update:
                statement = statement.with_for_update()
            rows = (await session.execute(statement)).all()
            return self._identity_from_rows(rows)

    async def provision_from_principal(self, principal: Principal, role: str) -> LocalIdentity:
        if role not in FORMAL_ROLES:
            raise ValueError("a formal role is required")
        async with self.sessions() as session, session.begin():
            dialect = session_dialect(session)
            await session.execute(
                insert_ignore(
                    dialect,
                    User,
                    {
                        "issuer": principal.issuer,
                        "subject": principal.subject,
                        "display_name": principal.display_name or principal.subject,
                        "email": principal.email,
                        "status": "active",
                    },
                    [User.issuer, User.subject],
                )
            )
            user = await session.scalar(
                select(User)
                .where(User.issuer == principal.issuer, User.subject == principal.subject)
                .with_for_update()
            )
            if user is None:
                raise RuntimeError("identity provisioning did not return a user")
            if user.status != "active":
                return LocalIdentity(
                    id=user.id,
                    issuer=user.issuer,
                    subject=user.subject,
                    display_name=user.display_name,
                    email=user.email,
                    status=user.status,
                    role=await self._formal_role(session, user.id),
                    synced_at=datetime.now(timezone.utc),
                )
            user.display_name = principal.display_name or principal.subject
            user.email = principal.email
            for role_name in sorted(FORMAL_ROLES):
                await session.execute(
                    insert_ignore(
                        dialect,
                        Role,
                        {"name": role_name},
                        [Role.name],
                    )
                )
            selected_role = await session.scalar(select(Role).where(Role.name == role))
            if selected_role is None:
                raise RuntimeError("formal role provisioning failed")
            await session.execute(
                delete(UserRole).where(
                    and_(
                        UserRole.user_id == user.id,
                        UserRole.role_id.in_(select(Role.id).where(Role.name.in_(FORMAL_ROLES))),
                    )
                )
            )
            await session.execute(
                insert_ignore(
                    dialect,
                    UserRole,
                    {"user_id": user.id, "role_id": selected_role.id},
                    [UserRole.user_id, UserRole.role_id],
                )
            )
            return LocalIdentity(
                id=user.id,
                issuer=user.issuer,
                subject=user.subject,
                display_name=user.display_name,
                email=user.email,
                status=user.status,
                role=role,
                synced_at=datetime.now(timezone.utc),
            )

    @staticmethod
    async def _formal_role(session, user_id):
        roles = list(
            await session.scalars(
                select(Role.name)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == user_id, Role.name.in_(FORMAL_ROLES))
            )
        )
        return roles[0] if len(roles) == 1 else None

    @staticmethod
    def _identity_from_rows(rows) -> LocalIdentity | None:
        if not rows:
            return None
        user = rows[0][0]
        formal_roles = {role_name for _, role_name in rows if role_name in FORMAL_ROLES}
        role = next(iter(formal_roles)) if len(formal_roles) == 1 else None
        return LocalIdentity(
            id=user.id,
            issuer=user.issuer,
            subject=user.subject,
            display_name=user.display_name,
            email=user.email,
            status=user.status,
            role=role,
            synced_at=user.synced_at,
        )


_MANAGED_ROLES = frozenset({"Admin", "User", "Analyst", "Viewer"})
_AUDIT_METADATA_KEYS = frozenset(
    {
        "changed_fields",
        "old_role",
        "new_role",
        "old_status",
        "new_status",
        "keycloak_subject",
        "result",
    }
)


class IdentityTransaction:
    def __init__(self, session):
        self.session = session

    async def get_by_id(self, identity_id: UUID) -> LocalIdentity | None:
        rows = (
            await self.session.execute(
                select(User, Role.name)
                .outerjoin(UserRole, UserRole.user_id == User.id)
                .outerjoin(Role, Role.id == UserRole.role_id)
                .where(User.id == identity_id)
                .with_for_update()
            )
        ).all()
        return self._identity_from_rows(rows)

    async def sync_authoritative(self, user: KeycloakUser, issuer: str) -> IdentitySync:
        now = datetime.now(timezone.utc)
        dialect = session_dialect(self.session)
        await self.session.execute(
            insert_ignore(
                dialect,
                User,
                {
                    "issuer": issuer,
                    "subject": user.subject,
                    "display_name": user.display_name,
                    "email": user.email,
                    "status": "active" if user.enabled else "disabled",
                    "synced_at": now,
                },
                [User.issuer, User.subject],
            )
        )
        local_user = await self.session.scalar(
            select(User)
            .where(User.issuer == issuer, User.subject == user.subject)
            .with_for_update()
        )
        if local_user is None:
            raise RuntimeError("authoritative identity sync did not return a user")
        old_rows = (
            await self.session.execute(
                select(User, Role.name)
                .outerjoin(UserRole, UserRole.user_id == User.id)
                .outerjoin(Role, Role.id == UserRole.role_id)
                .where(User.id == local_user.id)
            )
        ).all()
        previous = self._identity_from_rows(old_rows)
        new_status = "active" if user.enabled else "disabled"
        changed_fields = []
        if previous is None or previous.display_name != user.display_name:
            changed_fields.append("display_name")
        if previous is None or previous.email != user.email:
            changed_fields.append("email")
        if previous is None or previous.status != new_status:
            changed_fields.append("status")
        if previous is None or previous.role != user.role:
            changed_fields.append("role")
        local_user.display_name = user.display_name
        local_user.email = user.email
        local_user.status = new_status
        local_user.synced_at = now
        for role_name in sorted(FORMAL_ROLES):
            await self.session.execute(
                insert_ignore(
                    dialect,
                    Role,
                    {"name": role_name},
                    [Role.name],
                )
            )
        role_row = await self.session.scalar(select(Role).where(Role.name == user.role))
        if role_row is None:
            raise RuntimeError("formal role sync failed")
        await self.session.execute(
            delete(UserRole).where(
                UserRole.user_id == local_user.id,
                UserRole.role_id.in_(
                    select(Role.id).where(Role.name.in_(_MANAGED_ROLES))
                ),
            )
        )
        await self.session.execute(
            insert_ignore(
                dialect,
                UserRole,
                {"user_id": local_user.id, "role_id": role_row.id},
                [UserRole.user_id, UserRole.role_id],
            )
        )
        identity = LocalIdentity(
            id=local_user.id,
            issuer=local_user.issuer,
            subject=local_user.subject,
            display_name=local_user.display_name,
            email=local_user.email,
            status=local_user.status,
            role=user.role,
            synced_at=now,
        )
        return IdentitySync(
            identity=identity,
            changed_fields=tuple(changed_fields),
            old_role=previous.role if previous else None,
            old_status=previous.status if previous else None,
        )

    async def enabled_admin_count(self) -> int:
        count = await self.session.scalar(
            select(func.count(User.id))
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(User.status == "active", Role.name == "Admin")
        )
        return int(count or 0)

    async def append_audit(
        self,
        principal: Principal,
        action: str,
        target: LocalIdentity,
        request_id: str,
        metadata: dict,
    ) -> None:
        keys = set(metadata)
        if not keys <= _AUDIT_METADATA_KEYS or any(
            sensitive in key.casefold()
            for key in keys
            for sensitive in ("password", "secret", "token", "authorization")
        ):
            raise ValueError("identity audit metadata contains a forbidden field")
        self.session.add(
            AuditEvent(
                actor_type=principal.actor_type,
                actor_id=principal.subject,
                action=action,
                object_type="user",
                object_id=str(target.id),
                request_id=request_id,
                metadata_json=dict(metadata),
            )
        )
        await self.session.flush()

    @staticmethod
    def _identity_from_rows(rows) -> LocalIdentity | None:
        if not rows:
            return None
        user = rows[0][0]
        formal_roles = {role_name for _, role_name in rows if role_name in FORMAL_ROLES}
        role = next(iter(formal_roles)) if len(formal_roles) == 1 else None
        return LocalIdentity(
            id=user.id,
            issuer=user.issuer,
            subject=user.subject,
            display_name=user.display_name,
            email=user.email,
            status=user.status,
            role=role,
            synced_at=user.synced_at,
        )
