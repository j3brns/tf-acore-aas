"""Mock JWKS endpoint — FastAPI on :8766.

Endpoints:
    GET  /.well-known/jwks.json  Serve the test RSA public key as a JWK Set.
    POST /token                  Issue a signed RS256 JWT for a given tenant.
    GET  /health                 Service health check.

The RSA key pair is generated once on startup and is ephemeral (not persisted).
All JWTs issued by this service are valid only while the container is running.
"""

import base64
import logging
import time
import uuid

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="mock-jwks")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("mock-jwks")

# Ephemeral RSA-2048 key pair — generated once per container lifetime
_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()
_KID = str(uuid.uuid4())

# Issuer and audience used for all tokens from this mock
ISSUER = "http://localhost:8766"
AUDIENCE = "api://platform-local"


def _int_to_base64url(n: int) -> str:
    """Encode a big integer as a base64url string (for use in JWK 'n' and 'e')."""
    byte_length = (n.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(n.to_bytes(byte_length, "big")).rstrip(b"=").decode()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/.well-known/jwks.json")
def jwks() -> dict[str, list[dict[str, str]]]:
    """Serve the RSA public key as a JWK Set (JWKS)."""
    pub_numbers = _PUBLIC_KEY.public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "kid": _KID,
                "alg": "RS256",
                "n": _int_to_base64url(pub_numbers.n),
                "e": _int_to_base64url(pub_numbers.e),
            }
        ]
    }


class TokenRequest(BaseModel):
    tenant_id: str
    app_id: str = "platform-local"
    tier: str = "basic"
    sub: str = "test-user"
    roles: list[str] = []
    ttl: int = 3600


@app.post("/token")
def issue_token(req: TokenRequest) -> dict[str, object]:
    """Issue a signed RS256 JWT containing tenant context claims."""
    now = int(time.time())
    payload: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": req.sub,
        "iat": now,
        "nbf": now,
        "exp": now + req.ttl,
        # Tenant context claims — matched by authoriser Lambda
        "tenantid": req.tenant_id,
        "appid": req.app_id,
        "tier": req.tier,
        "roles": req.roles,
    }
    private_key_pem = _PRIVATE_KEY.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    token: str = jwt.encode(
        payload,
        private_key_pem,
        algorithm="RS256",
        headers={"kid": _KID},
    )
    logger.info(
        "Issued token | tenant_id=%s app_id=%s tier=%s ttl=%d",
        req.tenant_id,
        req.app_id,
        req.tier,
        req.ttl,
    )
    return {"access_token": token, "token_type": "Bearer", "expires_in": req.ttl}
