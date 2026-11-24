"""Apply subscription commands inside a caller-owned transaction."""

from __future__ import annotations

import ipaddress
import json
import re
import secrets
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from sqlalchemy import select

from .models import subscribers
from .store import InvalidInput, Unauthorized, new_id, utcnow
from .subscription_models import subscription_mail, subscription_preferences_keys
from .subscriptions import _email, _hash, _language, _topics

_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def _shape(payload, required, optional=()):
    if not isinstance(payload, dict):
        raise InvalidInput("payload")
    keys = set(payload)
    if not set(required) <= keys or not keys <= set(required) | set(optional):
        raise InvalidInput("payload keys")


def _token(payload):
    token = payload["token"]
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise Unauthorized("invalid token")
    return token


def _origin(public_origin):
    if not isinstance(public_origin, str) or not public_origin:
        raise InvalidInput("public_origin")
    parts = urlsplit(public_origin)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.path not in {"", "/"}
        or parts.hostname is None
    ):
        raise InvalidInput("public_origin")
    try:
        _ = parts.port
    except ValueError:
        raise InvalidInput("public_origin") from None
    if parts.scheme == "http" and parts.hostname != "localhost":
        try:
            if not ipaddress.ip_address(parts.hostname).is_loopback:
                raise InvalidInput("public_origin")
        except ValueError:
            raise InvalidInput("public_origin") from None
    return public_origin.rstrip("/")


def _subscribe(conn, payload, key, public_origin):
    _shape(payload, {"email", "topics"}, {"language"})
    address = _email(payload["email"])
    selected = _topics(payload["topics"])
    language = _language(payload.get("language", "en"))
    address_hash = _hash(address)
    found = conn.execute(
        select(subscribers.c.id).where(subscribers.c.address_hash == address_hash)
    ).first()
    if found is not None:
        return {"accepted": True}

    origin = _origin(public_origin)
    management_token = secrets.token_urlsafe(32)
    confirmation_token = secrets.token_urlsafe(32)
    now = utcnow()
    values = {
        "id": new_id(),
        "email": address,
        "address_hash": address_hash,
        "token_hash": _hash(management_token),
        "topics": selected,
        "language": language,
        "active": False,
        "confirmed": False,
        "confirmation_hash": _hash(confirmation_token),
        "created_at": now,
        "updated_at": now,
    }
    conn.execute(subscribers.insert().values(**values))
    encrypted_management_token = Fernet(key).encrypt(
        management_token.encode('ascii')
    ).decode('ascii')
    conn.execute(
        subscription_preferences_keys.insert().values(
            subscriber_id=values['id'],
            encrypted_token=encrypted_management_token,
        )
    )

    confirm_url = f"{origin}/communications/confirm/?token={confirmation_token}"
    preferences_url = (
        f"{origin}/communications/preferences/?token={management_token}"
    )
    message = {
        "subject": "Confirm your subscription",
        "body": (
            "Confirm your Townlight subscription:\n"
            f"{confirm_url}\n\n"
            "Manage your preferences:\n"
            f"{preferences_url}\n"
        ),
    }
    encrypted_body = Fernet(key).encrypt(
        json.dumps(message, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    conn.execute(
        subscription_mail.insert().values(
            id=new_id(),
            subscriber_id=values["id"],
            encrypted_body=encrypted_body,
            recipient=address,
            message_id=f"{new_id()}@townlight.local",
            state="pending",
            claim_token=None,
            lease_until=None,
            attempts=0,
            next_attempt_at=now,
            last_error=None,
            created_at=now,
        )
    )
    return {"accepted": True}


def _confirm(conn, payload):
    _shape(payload, {"token"})
    token_hash = _hash(_token(payload))
    row = conn.execute(
        select(subscribers.c.id)
        .where(subscribers.c.confirmation_hash == token_hash)
        .with_for_update()
    ).first()
    if row is None:
        raise Unauthorized("invalid token")
    conn.execute(
        subscribers.update()
        .where(subscribers.c.id == row.id)
        .values(
            confirmed=True,
            active=True,
            confirmation_hash=None,
            updated_at=utcnow(),
        )
    )
    return {"confirmed": True}


def _preferences(conn, payload):
    _shape(payload, {"token"}, {"topics", "language", "active"})
    token_hash = _hash(_token(payload))
    row = conn.execute(
        select(
            subscribers.c.id,
            subscribers.c.topics,
            subscribers.c.language,
            subscribers.c.active,
            subscribers.c.confirmed,
        )
        .where(subscribers.c.token_hash == token_hash)
        .with_for_update()
    ).first()
    if row is None or not row.confirmed:
        raise Unauthorized("invalid token")

    updates = {}
    if "topics" in payload:
        updates["topics"] = _topics(payload["topics"])
    if "language" in payload:
        updates["language"] = _language(payload["language"])
    if "active" in payload:
        if not isinstance(payload["active"], bool):
            raise InvalidInput("invalid active")
        updates["active"] = payload["active"]
    if updates:
        updates["updated_at"] = utcnow()
        conn.execute(
            subscribers.update()
            .where(subscribers.c.id == row.id)
            .values(**updates)
        )

    current = conn.execute(
        select(
            subscribers.c.topics,
            subscribers.c.language,
            subscribers.c.active,
        ).where(subscribers.c.id == row.id)
    ).first()
    if current is None:
        raise Unauthorized("invalid token")
    return {
        "topics": list(current.topics),
        "language": current.language,
        "active": current.active,
    }


def apply_subscription(conn, operation, payload, *, key, public_origin):
    """Apply one explicit subscription command using the supplied connection."""
    if operation == "comms_subscribe":
        return _subscribe(conn, payload, key, public_origin)
    if operation == "comms_confirm":
        return _confirm(conn, payload)
    if operation == "comms_preferences":
        return _preferences(conn, payload)
    raise InvalidInput("operation")


__all__ = ["apply_subscription"]
