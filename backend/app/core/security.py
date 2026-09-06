"""Cifrado de credenciales y generación de tokens.

Las credenciales de terceros (OAuth de Gmail/Microsoft, contraseñas SMTP,
claves de API) se guardan cifradas con Fernet. La clave maestra vive en el
entorno, nunca en la base de datos: si alguien se lleva un dump de Postgres,
no se lleva los buzones del usuario.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
import uuid
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.exceptions import ConfigurationError, DomainError


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = get_settings().encryption_key.get_secret_value()
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise ConfigurationError(
            "ENCRYPTION_KEY inválida. Generar con: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        ) from exc


def encrypt(plaintext: str) -> str:
    """Cifra un secreto para guardarlo en una columna `*_enc`."""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise ConfigurationError(
            "No se pudo descifrar la credencial. ¿Cambió ENCRYPTION_KEY?"
        ) from exc


def encrypt_optional(plaintext: str | None) -> str | None:
    return encrypt(plaintext) if plaintext else None


def decrypt_optional(ciphertext: str | None) -> str | None:
    return decrypt(ciphertext) if ciphertext else None


# ---------------------------------------------------------------- tokens


def new_tracking_token() -> uuid.UUID:
    """Token del pixel de apertura y de los links rastreados.

    UUID4: 122 bits de entropía. No es enumerable, que es justo lo que hace
    falta en un endpoint público sin autenticación.
    """
    return uuid.uuid4()


def new_unsubscribe_token() -> uuid.UUID:
    return uuid.uuid4()


def new_state_token() -> str:
    """`state` de OAuth, contra CSRF en el callback."""
    return secrets.token_urlsafe(32)


def constant_time_compare(a: str, b: str) -> bool:
    """Comparación sin fuga de tiempo, para validar tokens de webhook."""
    return secrets.compare_digest(a, b)


# ----------------------------------------------------------- service tokens (L1)

# Formato del token de portador diseñado en LOOP-03 (Bearer HMAC):
#
#   Authorization: Bearer <client_id>.<timestamp>.<nonce>.<signature>
#
#   - client_id : identidad del llamante de servicio (p.ej. "hermes").
#   - timestamp : segundos Unix del momento de emisión; TTL por defecto 300 s.
#   - nonce     : aleatorio de un solo uso, contra replay.
#   - signature : HMAC-SHA256 de "client_id.timestamp.nonce" con la clave
#                 SERVICE_TOKEN_KEY. No se puede fabricar sin la clave.


class ServiceTokenError(DomainError):
    """Token de servicio ausente o inválido."""


def _hmac_for(client_id: str, timestamp: int, nonce: str, key: str) -> str:
    message = f"{client_id}.{timestamp}.{nonce}".encode()
    return hmac.new(key.encode(), message, hashlib.sha256).hexdigest()


def issue_service_token(
    client_id: str,
    key: str,
    *,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> str:
    """Emite el token de `client_id` firmado con `key` (SERVICE_TOKEN_KEY).

    Solo para tests y futuros clientes de servicio: el server aún no emite
    tokens reales — PATCH-03 generará la credencial con aprobación.
    """
    ts = timestamp or int(time.time())
    n = nonce or uuid.uuid4().hex
    signature = _hmac_for(client_id, ts, n, key)
    return f"{client_id}.{ts}.{n}.{signature}"


class NonceStore:
    """Memoria de nonces ya vistos, para descartar replays dentro de la ventana.

    Estado en proceso: al reiniciar Mapache se vacía (un token repetido en un
    nuevo boot sería reaceptado dentro del TTL). Para producción multi-proceso
    se migrará a Redis — documentado como límite de esta capa L1 mínima.
    """

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl = ttl_seconds
        self._seen: dict[str, float] = {}

    def check_and_remember(self, nonce: str, now: float | None = None) -> bool:
        """True si el nonce es nuevo y aceptable; False si ya se vio dentro del TTL."""
        current = now if now is not None else time.time()
        for key in list(self._seen):
            if self._seen[key] < current:
                del self._seen[key]
        if nonce in self._seen:
            return False
        self._seen[nonce] = current + self._ttl
        return True

    def size(self) -> int:
        return len(self._seen)


def verify_service_token(
    token: str | None,
    *,
    key: str | None,
    ttl_seconds: int = 300,
    nonce_store: NonceStore | None = None,
    now: int | None = None,
    allowed_client_ids: frozenset[str] | set[str] | tuple[str, ...] | None = None,
) -> str:
    """Valida el token de portador y devuelve el `client_id`.

    Protecciones incluidas:
    - token ausente / formato inválido (≠<client>.<timestamp>.<nonce>.<sign>)
    - timestamp fuera de la ventana (expirado o futuro)
    - firma no coincidente
    - nonce repetido (replay) si se proporciona un `nonce_store`.
    - identidad no autorizada (si se pasa `allowed_client_ids`): solo acepta
      tokens cuyo `client_id` esté permitido (p.ej. solo "hermes").
    """
    if not token:
        raise ServiceTokenError(
            "Token de servicio ausente.",
            code="SERVICE_TOKEN_MISSING",
            details={},
        )
    parts = token.split(".")
    if len(parts) != 4:
        raise ServiceTokenError(
            "Token de servicio malformado.",
            code="SERVICE_TOKEN_INVALID",
            details={},
        )
    client_id, ts_raw, nonce, signature_given = parts
    if key is None:
        raise ServiceTokenError(
            "Clave de servicio no configurada.",
            code="SERVICE_TOKEN_KEY_MISSING",
            details={},
        )
    try:
        timestamp = int(ts_raw)
    except ValueError as exc:
        raise ServiceTokenError(
            "Timestamp de token inválido.",
            code="SERVICE_TOKEN_INVALID_TIMESTAMP",
            details={},
        ) from exc

    ref_now = now if now is not None else int(time.time())
    age = abs(ref_now - timestamp)
    if age > ttl_seconds:
        raise ServiceTokenError(
            "Token de servicio expirado o fuera de la ventana temporal.",
            code="SERVICE_TOKEN_EXPIRED",
            details={"age_seconds": age, "now": ref_now},
        )

    expected = _hmac_for(client_id, timestamp, nonce, key)
    if not constant_time_compare(expected, signature_given):
        raise ServiceTokenError(
            "Firma del token de servicio no válida.",
            code="SERVICE_TOKEN_BAD_SIGNATURE",
            details={},
        )

    if allowed_client_ids is not None and client_id not in allowed_client_ids:
        raise ServiceTokenError(
            "Identidad de servicio no autorizada.",
            code="SERVICE_TOKEN_UNKNOWN_CLIENT",
            details={"client_id": client_id},
        )

    if nonce_store is not None and not nonce_store.check_and_remember(nonce, ref_now):
        raise ServiceTokenError(
            "Token de servicio repetido (replay de nonce).",
            code="SERVICE_TOKEN_REPLAY",
            details={},
        )

    return client_id
