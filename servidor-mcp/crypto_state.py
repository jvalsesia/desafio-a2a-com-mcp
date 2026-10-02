import base64
import hashlib
import hmac
import json
import os
import time

SECRET_ENV = "REQUEST_STATE_SECRET"


class InvalidRequestStateError(Exception):
    """Raised when requestState fails verification or is expired."""
    pass


def get_secret() -> bytes:
    secret = os.environ.get(SECRET_ENV)
    if not secret:
        raise RuntimeError(f"A variavel de ambiente {SECRET_ENV} nao foi definida.")
    secret_bytes = secret.strip().encode("utf-8")
    if len(secret_bytes) < 32:
        raise RuntimeError(f"{SECRET_ENV} deve ter no minimo 32 bytes.")
    return secret_bytes


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    rem = len(data) % 4
    if rem > 0:
        data += "=" * (4 - rem)
    return base64.urlsafe_b64decode(data.encode("utf-8"))


def seal_request_state(data: dict, ttl_seconds: int = 900) -> str:
    """Seals data with HMAC-SHA256 and an expiration timestamp (default 15 minutes)."""
    secret = get_secret()
    payload = {
        "data": data,
        "exp": int(time.time()) + ttl_seconds,
    }
    payload_raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = _b64url_encode(payload_raw)

    sig = hmac.new(secret, payload_b64.encode("utf-8"), hashlib.sha256).digest()
    sig_b64 = _b64url_encode(sig)

    return f"v1.{payload_b64}.{sig_b64}"


def unseal_request_state(token: str) -> dict:
    """Verifies HMAC signature and expiration, returning sealed data."""
    secret = get_secret()
    if not token or not isinstance(token, str):
        raise InvalidRequestStateError("requestState ausente ou invalido.")

    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        raise InvalidRequestStateError("Formato de requestState invalido.")

    payload_b64 = parts[1]
    sig_b64 = parts[2]

    try:
        expected_sig = hmac.new(secret, payload_b64.encode("utf-8"), hashlib.sha256).digest()
        provided_sig = _b64url_decode(sig_b64)
    except Exception as e:
        raise InvalidRequestStateError(f"Falha ao decodificar assinatura do requestState: {e}")

    if not hmac.compare_digest(expected_sig, provided_sig):
        raise InvalidRequestStateError("Assinatura do requestState invalida / adulterada.")

    try:
        payload_bytes = _b64url_decode(payload_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception as e:
        raise InvalidRequestStateError(f"Falha ao decodificar conteudo do requestState: {e}")

    exp = payload.get("exp")
    if exp is None or time.time() > exp:
        raise InvalidRequestStateError("requestState expirou.")

    return payload.get("data", {})
