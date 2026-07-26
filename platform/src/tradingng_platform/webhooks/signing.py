import hashlib
import hmac
import json

from cryptography.fernet import Fernet, InvalidToken


class SecretCipher:
    """Encrypt webhook signing secrets at rest with a platform-owned Fernet key."""

    def __init__(self, key: str | bytes):
        encoded = key.encode("ascii") if isinstance(key, str) else key
        try:
            self._fernet = Fernet(encoded)
        except (TypeError, ValueError) as exc:
            raise ValueError("webhook encryption key must be a valid Fernet key") from exc

    def encrypt(self, secret: str | bytes) -> str:
        raw = secret.encode("utf-8") if isinstance(secret, str) else secret
        if len(raw) < 16:
            raise ValueError("webhook signing secret must contain at least 16 bytes")
        return self._fernet.encrypt(raw).decode("ascii")

    def decrypt(self, encrypted_secret: str) -> bytes:
        try:
            return self._fernet.decrypt(encrypted_secret.encode("ascii"))
        except (InvalidToken, UnicodeEncodeError) as exc:
            raise ValueError("webhook signing secret cannot be decrypted") from exc


def canonical_json(payload: dict) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def signature_headers(
    secret: bytes,
    event_id: str,
    body: bytes,
    timestamp: int,
) -> dict[str, str]:
    signed = f"{timestamp}.{event_id}.".encode() + body
    digest = hmac.new(secret, signed, hashlib.sha256).hexdigest()
    return {
        "X-TradingNG-Event-ID": event_id,
        "X-TradingNG-Timestamp": str(timestamp),
        "X-TradingNG-Signature": f"v1={digest}",
    }
