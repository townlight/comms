"""Reconcile queued publications from caller-supplied queue acknowledgements."""

from __future__ import annotations

from sqlalchemy import and_, case, func, insert, or_, select, update

from .models import articles, email_outbox, events, publications
from .store import Conflict, InvalidInput, utcnow

_RECEIPT_STATES = ("delivered", "withdrawn", "rejected", "superseded")
_FAILED = "publication failed"
_UNCERTAIN = "publication outcome uncertain"


def _channel(channels, name):
    if isinstance(channels, dict):
        return bool(channels.get(name))
    return name in (channels or ())


def _receipt(value):
    if not isinstance(value, dict):
        return None
    keys = ("status", "url", "queue_sequence", "queue_digest")
    return {key: value[key] for key in keys if key in value}


class PublicationReconciliationMixin:
    def queued_publications(self, limit=20):
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise InvalidInput("limit")
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(publications)
                .where(publications.c.state == "queued")
                .order_by(
                    publications.c.due_at,
                    publications.c.created_at,
                    publications.c.id,
                )
                .limit(limit)
            ).mappings().all()
        return [dict(row) for row in rows]

    @staticmethod
    def _email_counts(conn, publication_id):
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
        row = conn.execute(
            select(
                func.coalesce(func.sum(case((email_outbox.c.state == "sent", 1), else_=0)), 0).label("sent"),
                func.coalesce(func.sum(case((email_outbox.c.state.in_(("pending", "claimed")), 1), else_=0)), 0).label("remaining"),
                func.coalesce(func.sum(case((failed, 1), else_=0)), 0).label("failed"),
                func.coalesce(func.sum(case((email_outbox.c.state == "uncertain", 1), else_=0)), 0).label("uncertain"),
                func.coalesce(func.sum(case((skipped, 1), else_=0)), 0).label("skipped"),
            ).where(email_outbox.c.publication_id == publication_id)
        ).mappings().one()
        return {key: int(row[key]) for key in ("sent", "remaining", "failed", "uncertain", "skipped")}

    def email_summary(self, publication_id):
        with self.engine.connect() as conn:
            return self._email_counts(conn, publication_id)

    def reconcile_publication(self, job_id, website_receipt=None):
        now = utcnow()
        with self.engine.begin() as conn:
            job = conn.execute(
                select(publications)
                .where(publications.c.id == job_id, publications.c.state == "queued")
                .with_for_update()
            ).mappings().first()
            if job is None:
                raise Conflict("publication is not queued")

            channels = job["channels"] or ()
            website_requested = _channel(channels, "website")
            email_requested = _channel(channels, "email")
            summary = self._email_counts(conn, job_id)
            stored = job["website_receipt"]
            stored_receipt = stored if isinstance(stored, dict) else {}
            ack = None
            website_state = "ok"

            if website_requested:
                if not isinstance(website_receipt, dict):
                    raise InvalidInput("website receipt is required")
                status = website_receipt.get("status")
                url = website_receipt.get("url")
                if not url:
                    raise InvalidInput("website receipt is incomplete")
                if stored_receipt.get("queue_sequence") is None or not stored_receipt.get("url"):
                    raise InvalidInput("stored queue receipt is incomplete")
                if website_receipt.get("queue_sequence") != stored_receipt["queue_sequence"]:
                    raise Conflict("queue sequence mismatch")
                stored_digest = stored_receipt.get("queue_digest")
                supplied_digest = website_receipt.get("queue_digest")
                if stored_digest is not None and supplied_digest is not None and stored_digest != supplied_digest:
                    raise Conflict("queue digest mismatch")
                if url != stored_receipt["url"]:
                    raise Conflict("queue url changed")
                expected = "withdrawn" if job["action"] == "withdraw" else "delivered"
                if status == "queued":
                    website_state = "queued"
                elif status not in _RECEIPT_STATES:
                    raise InvalidInput("invalid website receipt status")
                elif status in ("rejected", "superseded") or status != expected:
                    website_state = "failed"
                ack = _receipt(website_receipt)

            email_state = "ok"
            if email_requested:
                if summary["failed"] > 0:
                    email_state = "failed"
                elif summary["uncertain"] > 0:
                    email_state = "uncertain"
                elif summary["remaining"] > 0:
                    email_state = "queued"

            if website_state == "failed" or email_state == "failed":
                final = "failed"
            elif email_state == "uncertain":
                final = "uncertain"
            elif website_state == "queued" or email_state == "queued":
                final = "queued"
            else:
                final = "published"

            receipt = ack if ack is not None else _receipt(stored)
            if final == "queued":
                result = conn.execute(
                    update(publications)
                    .where(publications.c.id == job_id, publications.c.state == "queued")
                    .values(
                        website_receipt=receipt,
                        email_summary=summary,
                        updated_at=now,
                    )
                )
                if result.rowcount != 1:
                    raise Conflict("publication changed")
                return {"job_id": job_id, "state": "queued", "email_summary": summary, "website_receipt": receipt}

            result = conn.execute(
                update(publications)
                .where(publications.c.id == job_id, publications.c.state == "queued")
                .values(
                    state=final,
                    website_receipt=receipt,
                    email_summary=summary,
                    last_error=_FAILED if final == "failed" else _UNCERTAIN if final == "uncertain" else None,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                raise Conflict("publication changed")
            if final == "published":
                article_state = "withdrawn" if job["action"] == "withdraw" else "published"
                conn.execute(
                    update(articles)
                    .where(
                        articles.c.id == job["article_id"],
                        articles.c.current_revision == job["revision"],
                    )
                    .values(state=article_state, updated_at=now)
                )
            conn.execute(
                insert(events).values(
                    article_id=job["article_id"],
                    revision=job["revision"],
                    kind="publication_reconciled",
                    actor=job["actor"],
                    payload={"publication_id": job_id, "action": job["action"], "state": final},
                    created_at=now,
                )
            )
            return {"job_id": job_id, "state": final, "email_summary": summary, "website_receipt": receipt}
