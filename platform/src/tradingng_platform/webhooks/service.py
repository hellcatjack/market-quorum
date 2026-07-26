import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tradingng_platform.assessments.repository import AssessmentRepository
from tradingng_platform.auth.principal import Principal
from tradingng_platform.models import User, Webhook
from tradingng_platform.webhooks.contracts import CreateWebhook, WebhookView
from tradingng_platform.webhooks.signing import SecretCipher
from tradingng_platform.webhooks.worker import validate_endpoint


class WebhookNotFound(LookupError):
    pass


class WebhookService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        encryption_key: str,
        private_host_allowlist: tuple[str, ...] = (),
    ):
        self.sessions = sessions
        self.cipher = SecretCipher(encryption_key)
        self.private_host_allowlist = frozenset(private_host_allowlist)

    async def create(
        self,
        principal: Principal,
        command: CreateWebhook,
        request_id: str,
    ) -> WebhookView:
        self._require_admin(principal)
        endpoint = await validate_endpoint(
            command.endpoint,
            self.private_host_allowlist,
        )
        encrypted_secret = self.cipher.encrypt(command.secret.get_secret_value())
        async with self.sessions() as session, session.begin():
            user = await AssessmentRepository(session).upsert_user(principal)
            webhook = Webhook(
                owner_id=user.id,
                endpoint=str(endpoint),
                event_types_json=sorted(command.event_types),
                encrypted_secret=encrypted_secret,
                status="active",
            )
            session.add(webhook)
            await session.flush()
            await AssessmentRepository(session).append_audit(
                principal,
                "webhook.create",
                "webhook",
                str(webhook.id),
                request_id,
                {
                    "endpoint": webhook.endpoint,
                    "event_types": webhook.event_types_json,
                },
            )
            return self._view(webhook)

    async def list(self, principal: Principal) -> list[WebhookView]:
        self._require_admin(principal)
        async with self.sessions() as session:
            owner_id = await self._owner_id(session, principal)
            if owner_id is None:
                return []
            hooks = list(
                await session.scalars(
                    select(Webhook)
                    .where(Webhook.owner_id == owner_id)
                    .order_by(Webhook.created_at.desc(), Webhook.id.desc())
                )
            )
            return [self._view(webhook) for webhook in hooks]

    async def deactivate(
        self,
        principal: Principal,
        webhook_id: uuid.UUID,
        request_id: str,
    ) -> None:
        self._require_admin(principal)
        async with self.sessions() as session, session.begin():
            owner_id = await self._owner_id(session, principal)
            webhook = await session.scalar(
                select(Webhook)
                .where(Webhook.id == webhook_id, Webhook.owner_id == owner_id)
                .with_for_update()
            )
            if webhook is None:
                raise WebhookNotFound
            if webhook.status != "disabled":
                webhook.status = "disabled"
                await AssessmentRepository(session).append_audit(
                    principal,
                    "webhook.disable",
                    "webhook",
                    str(webhook.id),
                    request_id,
                    {"endpoint": webhook.endpoint},
                )

    @staticmethod
    async def _owner_id(session: AsyncSession, principal: Principal) -> uuid.UUID | None:
        return await session.scalar(
            select(User.id).where(
                User.issuer == principal.issuer,
                User.subject == principal.subject,
            )
        )

    @staticmethod
    def _require_admin(principal: Principal) -> None:
        principal.require("assessments:admin")
        if "Admin" not in principal.roles:
            raise PermissionError("Admin role is required to manage webhooks")

    @staticmethod
    def _view(webhook: Webhook) -> WebhookView:
        return WebhookView(
            id=webhook.id,
            endpoint=webhook.endpoint,
            event_types=set(webhook.event_types_json),
            status=webhook.status,
            created_at=webhook.created_at,
        )
