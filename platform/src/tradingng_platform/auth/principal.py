from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    issuer: str
    subject: str
    actor_type: str
    scopes: frozenset[str]
    display_name: str = ""
    email: str | None = None
    roles: frozenset[str] = frozenset()

    def require(self, *required: str) -> None:
        missing = set(required) - self.scopes
        if missing:
            raise PermissionError(f"missing scopes: {', '.join(sorted(missing))}")
