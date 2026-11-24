from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, case, func, or_, select, update

from .models import approvals, articles, email_outbox, publications, subscribers
from .store import InvalidInput

_SKIP = "skipped: not deliverable"
_STALE = "publication revision outdated"
_INACTIVE = "skipped: subscriber inactive or unconfirmed"
_EXPIRED = "lease expired"
_RETRY = "delivery retry scheduled"
_FAILED = "delivery failed"
_UNCERTAIN = "delivery outcome uncertain"
_MAX_ATTEMPTS = 5
_MAX_BACKOFF = 3600
_MAX_SKIP_ITERATIONS = 100


def _utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class EmailClaimsMixin:
    def claim_email(self, now=None, lease_seconds=120):
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or not 30 <= lease_seconds <= 600
        ):
            raise InvalidInput("lease_seconds")

        now = _utc(now) or datetime.now(UTC)
        lease_until = now + timedelta(seconds=lease_seconds)
        dialect = self.engine.dialect.name

        with self.engine.begin() as conn:
            conn.execute(
                update(email_outbox)
                .where(
                    and_(
                        email_outbox.c.state == "claimed",
                        email_outbox.c.lease_until.is_not(None),
                        email_outbox.c.lease_until <= now,
                    )
                )
                .values(
                    state="uncertain",
                    last_error=_EXPIRED,
                    claim_token=None,
                    lease_until=None,
                )
            )

            for _ in range(_MAX_SKIP_ITERATIONS):
                stmt = (
                    select(
                        email_outbox.c.id,
                        email_outbox.c.recipient,
                        email_outbox.c.subject,
                        email_outbox.c.body,
                        email_outbox.c.message_id,
                        email_outbox.c.attempts,
                        email_outbox.c.state,
                        subscribers.c.active,
                        subscribers.c.confirmed,
                        publications.c.revision.label("publication_revision"),
                        publications.c.channels,
                        publications.c.article_id,
                        articles.c.current_revision.label("article_revision"),
                        approvals.c.revision.label("approval_revision"),
                    )
                    .select_from(
                        email_outbox.join(
                            subscribers,
                            subscribers.c.id == email_outbox.c.subscriber_id,
                        )
                        .join(
                            publications,
                            publications.c.id == email_outbox.c.publication_id,
                        )
                        .join(articles, articles.c.id == publications.c.article_id)
                        .outerjoin(
                            approvals,
                            and_(
                                approvals.c.article_id == publications.c.article_id,
                                approvals.c.revision == publications.c.revision,
                            ),
                        )
                    )
                    .where(
                        and_(
                            email_outbox.c.state == "pending",
                            or_(
                                email_outbox.c.next_attempt_at.is_(None),
                                email_outbox.c.next_attempt_at <= now,
                            ),
                        )
                    )
                    .order_by(
                        email_outbox.c.next_attempt_at,
                        email_outbox.c.created_at,
                    )
                    .limit(1)
                )
                if dialect == "postgresql":
                    stmt = stmt.with_for_update(skip_locked=True, of=email_outbox)
                row = conn.execute(stmt).mappings().first()
                if row is None:
                    return None

                email_id = row["id"]
                if row["attempts"] >= _MAX_ATTEMPTS:
                    self._terminal(conn, email_id, _FAILED)
                    continue
                if not row["active"] or not row["confirmed"]:
                    self._terminal(conn, email_id, _INACTIVE, skipped=True)
                    continue

                channels = row["channels"]
                if not isinstance(channels, list) or "email" not in channels:
                    self._terminal(conn, email_id, _SKIP, skipped=True)
                    continue
                if (
                    row["article_revision"] != row["publication_revision"]
                    or row["approval_revision"] is None
                ):
                    self._terminal(conn, email_id, _STALE)
                    continue

                token = str(uuid.uuid4())
                stale_ok = and_(
                    email_outbox.c.id == email_id,
                    email_outbox.c.state == "pending",
                    or_(
                        email_outbox.c.next_attempt_at.is_(None),
                        email_outbox.c.next_attempt_at <= now,
                    ),
                )
                result = conn.execute(
                    update(email_outbox)
                    .where(stale_ok)
                    .values(
                        state="claimed",
                        claim_token=token,
                        attempts=email_outbox.c.attempts + 1,
                        lease_until=lease_until,
                        next_attempt_at=None,
                        last_error=None,
                    )
                )
                if result.rowcount != 1:
                    continue
                return {
                    "id": email_id,
                    "claim_token": token,
                    "recipient": row["recipient"],
                    "subject": row["subject"],
                    "body": row["body"],
                    "message_id": row["message_id"],
                }

            return None

    def finish_email(self, email_id, claim_token, outcome):
        now = datetime.now(UTC)
        with self.engine.begin() as conn:
            row = (
                conn.execute(
                    select(email_outbox.c.attempts)
                    .where(
                        and_(
                            email_outbox.c.id == email_id,
                            email_outbox.c.state == "claimed",
                            email_outbox.c.claim_token == claim_token,
                        )
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if row is None:
                return False

            values = {
                "claim_token": None,
                "lease_until": None,
                "next_attempt_at": None,
            }
            if outcome == "sent":
                values.update(state="sent", sent_at=now, last_error=None)
            elif outcome == "retry":
                if row["attempts"] >= _MAX_ATTEMPTS:
                    values.update(state="failed", last_error=_FAILED)
                else:
                    delay = min(_MAX_BACKOFF, 2 ** min(row["attempts"], 12))
                    values.update(
                        state="pending",
                        next_attempt_at=now + timedelta(seconds=delay),
                        last_error=_RETRY,
                    )
            elif outcome == "failed":
                values.update(state="failed", last_error=_FAILED)
            elif outcome == "uncertain":
                values.update(state="uncertain", last_error=_UNCERTAIN)
            elif outcome == "skipped":
                values.update(state="failed", last_error=_SKIP)
            else:
                raise ValueError("invalid email outcome")

            result = conn.execute(
                update(email_outbox)
                .where(
                    and_(
                        email_outbox.c.id == email_id,
                        email_outbox.c.state == "claimed",
                        email_outbox.c.claim_token == claim_token,
                    )
                )
                .values(**values)
            )
            return result.rowcount == 1

    def email_summary(self, publication_id):
        skipped = and_(
            email_outbox.c.state == "failed",
            email_outbox.c.last_error.like("skipped%"),
        )
        failed = and_(
            email_outbox.c.state == "failed",
            or_(
                email_outbox.c.last_error.is_(None),
                ~email_outbox.c.last_error.like("skipped%"),
            ),
        )
        with self.engine.connect() as conn:
            row = conn.execute(
                select(
                    func.coalesce(
                        func.sum(case((email_outbox.c.state == "sent", 1), else_=0)),
                        0,
                    ).label("sent"),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    email_outbox.c.state.in_(("pending", "claimed")),
                                    1,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ).label("remaining"),
                    func.coalesce(
                        func.sum(case((failed, 1), else_=0)), 0
                    ).label("failed"),
                    func.coalesce(
                        func.sum(
                            case((email_outbox.c.state == "uncertain", 1), else_=0)
                        ),
                        0,
                    ).label("uncertain"),
                    func.coalesce(
                        func.sum(case((skipped, 1), else_=0)), 0
                    ).label("skipped"),
                ).where(email_outbox.c.publication_id == publication_id)
            ).mappings().one()
        return {key: int(row[key]) for key in ("sent", "remaining", "failed", "uncertain", "skipped")}

    @staticmethod
    def _terminal(conn, email_id, error, skipped=False):
        _ = skipped
        conn.execute(
            update(email_outbox)
            .where(
                and_(
                    email_outbox.c.id == email_id,
                    email_outbox.c.state == "pending",
                )
            )
            .values(
                state="failed",
                last_error=error,
                claim_token=None,
                lease_until=None,
                next_attempt_at=None,
            )
        )
