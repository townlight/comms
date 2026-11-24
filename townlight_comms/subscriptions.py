import hashlib
import secrets
from email.utils import parseaddr

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .models import subscribers
from .store import InvalidInput, Unauthorized, new_id, utcnow

_ALLOWED = frozenset({"all", "meetings", "code", "notice", "311", "parks"})
_COLS = (subscribers.c.id, subscribers.c.topics,
         subscribers.c.language, subscribers.c.active, subscribers.c.confirmed)


def _hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _email(value):
    if not isinstance(value, str):
        raise InvalidInput("invalid email")
    addr = value.strip()
    if not addr or "\r" in addr or "\n" in addr:
        raise InvalidInput("invalid email")
    name, parsed = parseaddr(addr)
    local, _, domain = parsed.rpartition("@")
    if name or not local or not domain or "." not in domain or parsed != addr:
        raise InvalidInput("invalid email")
    return addr.lower()


def _topics(value):
    if not isinstance(value, (list, tuple, set)):
        raise InvalidInput("invalid topics")
    items = list(value)
    if not items or len(items) > 6 or len(set(items)) != len(items):
        raise InvalidInput("invalid topics")
    if any(not isinstance(t, str) or t not in _ALLOWED for t in items):
        raise InvalidInput("invalid topics")
    return items


def _language(value):
    if not isinstance(value, str) or not value or len(value) > 16:
        raise InvalidInput("invalid language")
    return value


class SubscriptionMixin:
    def request_subscription(self, email, topics, language="en"):
        address = _email(email)
        selected = _topics(topics)
        language = _language(language)
        address_hash = _hash(address)
        generic = {"accepted": True}
        try:
            with self.engine.begin() as conn:
                found = conn.execute(select(subscribers.c.id).where(
                    subscribers.c.address_hash == address_hash)).first()
                if found:
                    return generic
                management_token = secrets.token_urlsafe(32)
                confirmation_token = secrets.token_urlsafe(32)
                now = utcnow()
                values = {"id": new_id(), "email": address,
                          "address_hash": address_hash,
                          "token_hash": _hash(management_token),
                          "topics": selected, "language": language,
                          "active": False, "confirmed": False,
                          "confirmation_hash": _hash(confirmation_token),
                          "created_at": now, "updated_at": now}
                conn.execute(subscribers.insert().values(**values))
        except IntegrityError:
            return generic
        return {"accepted": True, "id": values["id"],
                "management_token": management_token,
                "confirmation_token": confirmation_token}

    def confirm_subscription(self, token):
        if not isinstance(token, str) or not token:
            raise Unauthorized("invalid token")
        with self.engine.begin() as conn:
            row = conn.execute(select(subscribers.c.id).where(
                subscribers.c.confirmation_hash == _hash(token))).first()
            if not row:
                raise Unauthorized("invalid token")
            conn.execute(subscribers.update().where(
                subscribers.c.id == row.id).values(confirmed=True, active=True,
                confirmation_hash=None, updated_at=utcnow()))
        return {"confirmed": True}

    def preferences(self, token, topics=None, language=None, active=None):
        if not isinstance(token, str) or not token:
            raise Unauthorized("invalid token")
        with self.engine.begin() as conn:
            row = conn.execute(select(*_COLS).where(
                subscribers.c.token_hash == _hash(token))).first()
            if not row or not row.confirmed:
                raise Unauthorized("invalid token")
            updates = {}
            if topics is not None:
                updates["topics"] = _topics(topics)
            if language is not None:
                updates["language"] = _language(language)
            if active is not None:
                if not isinstance(active, bool):
                    raise InvalidInput("invalid active")
                updates["active"] = active
            if updates:
                updates["updated_at"] = utcnow()
                conn.execute(subscribers.update().where(
                    subscribers.c.id == row.id).values(**updates))
            current = conn.execute(select(*_COLS).where(
                subscribers.c.id == row.id)).first()
            if not current:
                raise Unauthorized("invalid token")
            return {"id": current.id, "topics": list(current.topics),
                    "language": current.language, "active": current.active}
