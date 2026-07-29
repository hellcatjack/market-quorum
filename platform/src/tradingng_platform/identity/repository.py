from datetime import datetime, timezone

from sqlalchemy import and_, delete, select

from tradingng_platform.auth.oidc import FORMAL_ROLES
from tradingng_platform.auth.principal import Principal
from tradingng_platform.identity.contracts import LocalIdentity
from tradingng_platform.models import Role, User, UserRole
from tradingng_platform.persistence.upsert import insert_ignore, session_dialect


class IdentityRepository:
    def __init__(self, sessions):
        self.sessions = sessions

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
            synced_at=datetime.now(timezone.utc),
        )
