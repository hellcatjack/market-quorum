from tradingng_platform.api.errors import ApiError
from tradingng_platform.auth.oidc import ADMIN_SCOPES, FORMAL_ROLES, USER_SCOPES
from tradingng_platform.auth.principal import Principal


class IdentityAccessService:
    def __init__(self, repository):
        self.repository = repository

    async def enforce(self, principal: Principal) -> Principal:
        if principal.actor_type != "user":
            return principal

        identity = await self.repository.get_human(principal.issuer, principal.subject)
        token_roles = principal.roles & FORMAL_ROLES
        if identity is None:
            if len(token_roles) != 1:
                raise ApiError(
                    403,
                    "identity_not_provisioned",
                    "This account does not have a supported platform role",
                )
            identity = await self.repository.provision_from_principal(
                principal,
                next(iter(token_roles)),
            )

        if identity.status != "active":
            raise ApiError(403, "account_disabled", "This account is disabled")
        if identity.role not in FORMAL_ROLES or len(token_roles) != 1:
            raise ApiError(
                403,
                "identity_not_provisioned",
                "This account does not have a supported platform role",
            )

        token_role = next(iter(token_roles))
        effective_role = "Admin" if token_role == identity.role == "Admin" else "User"
        ceiling = ADMIN_SCOPES if effective_role == "Admin" else USER_SCOPES
        return Principal(
            issuer=principal.issuer,
            subject=principal.subject,
            actor_type=principal.actor_type,
            scopes=principal.scopes & ceiling,
            display_name=identity.display_name,
            email=identity.email,
            roles=frozenset({effective_role}),
        )
