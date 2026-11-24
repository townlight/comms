from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import and_, select, update

from .store import InvalidInput, new_id, utcnow
from .subscription_models import subscription_mail

_MAX_ATTEMPTS = 5
_MAX_BACKOFF = 3600
_EXPIRED = "lease expired"
_RETRY = "delivery retry scheduled"
_FAILED = "delivery failed"
_UNCERTAIN = "delivery outcome uncertain"


def _utc(value):
    if value is None:
        return utcnow()
    if not isinstance(value, datetime):
        raise InvalidInput("now")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SubscriptionDeliveryMixin:
    def claim_subscription_email(self, *, key, now=None, lease_seconds=120):
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 30 <= lease_seconds <= 600:
            raise InvalidInput("lease_seconds")
        if not isinstance(key, (bytes, str)) or not key:
            raise InvalidInput("key")
        now = _utc(now)
        lease_until = now + timedelta(seconds=lease_seconds)
        with self.engine.begin() as conn:
            conn.execute(update(subscription_mail).where(
                and_(subscription_mail.c.state == "claimed",
                     subscription_mail.c.lease_until.is_not(None),
                     subscription_mail.c.lease_until <= now)
            ).values(state="uncertain", last_error=_EXPIRED, claim_token=None,
                     lease_until=None, next_attempt_at=now))
            stmt = (select(subscription_mail.c.id, subscription_mail.c.encrypted_body,
                           subscription_mail.c.recipient, subscription_mail.c.message_id)
                    .where(and_(subscription_mail.c.state == "pending",
                                subscription_mail.c.next_attempt_at <= now))
                    .order_by(subscription_mail.c.next_attempt_at,
                              subscription_mail.c.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True, of=subscription_mail))
            row = conn.execute(stmt).mappings().first()
            if row is None:
                return None
            token = new_id()
            result = conn.execute(update(subscription_mail).where(and_(
                subscription_mail.c.id == row["id"],
                subscription_mail.c.state == "pending",
            )).values(state="claimed", claim_token=token, lease_until=lease_until,
                      next_attempt_at=now, last_error=None,
                      attempts=subscription_mail.c.attempts + 1))
            if result.rowcount != 1:
                return None
            claimed = dict(row)
        try:
            payload = json.loads(Fernet(key).decrypt(
                claimed["encrypted_body"].encode("utf-8")
            ).decode("utf-8"))
            subject, body = payload["subject"], payload["body"]
            if not isinstance(subject, str) or not isinstance(body, str):
                raise TypeError
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            self.finish_subscription_email(claimed["id"], token, "failed", now=now)
            raise InvalidInput("subscription mail payload") from None
        return {"id": claimed["id"], "claim_token": token, "recipient": claimed["recipient"],
                "message_id": claimed["message_id"], "subject": subject, "body": body}

    def finish_subscription_email(self, id, claim_token, outcome, now=None):
        if not isinstance(id, str) or not id:
            raise InvalidInput("id")
        if not isinstance(claim_token, str) or not claim_token:
            raise InvalidInput("claim_token")
        if outcome not in ("sent", "retry", "failed", "uncertain"):
            raise InvalidInput("outcome")
        now = _utc(now)
        values = {"claim_token": None, "lease_until": None, "next_attempt_at": now}
        with self.engine.begin() as conn:
            if outcome == "retry":
                row = conn.execute(select(subscription_mail.c.attempts).where(and_(
                    subscription_mail.c.id == id,
                    subscription_mail.c.state == "claimed",
                    subscription_mail.c.claim_token == claim_token,
                )).limit(1)).mappings().first()
                if row is None:
                    return False
                if row["attempts"] >= _MAX_ATTEMPTS:
                    values.update(state="failed", last_error=_FAILED)
                else:
                    delay = min(_MAX_BACKOFF, 2 ** min(row["attempts"], 12))
                    values.update(state="pending",
                                  next_attempt_at=now + timedelta(seconds=delay),
                                  last_error=_RETRY)
            elif outcome == "sent":
                values.update(state="sent", last_error=None)
            elif outcome == "failed":
                values.update(state="failed", last_error=_FAILED)
            else:
                values.update(state="uncertain", last_error=_UNCERTAIN)
            result = conn.execute(update(subscription_mail).where(and_(
                subscription_mail.c.id == id,
                subscription_mail.c.state == "claimed",
                subscription_mail.c.claim_token == claim_token,
            )).values(**values))
            return result.rowcount == 1
