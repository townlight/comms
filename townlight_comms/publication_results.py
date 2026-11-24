"""Publication result finalization using SQLAlchemy Core."""

from __future__ import annotations

from sqlalchemy import insert, select, update

from .models import articles, events, publications
from .store import Conflict, InvalidInput, utcnow

SANITIZED_ERROR = "publication failed"
_FINAL_STATES = ("queued", "published", "failed", "uncertain")


def _event(conn, *, article_id, revision, kind, actor, payload, created_at):
    conn.execute(
        insert(events).values(
            article_id=article_id,
            revision=revision,
            kind=kind,
            actor=actor,
            payload=payload,
            created_at=created_at,
        )
    )


def _channel(channels, name):
    if isinstance(channels, dict):
        return bool(channels.get(name))
    return name in (channels or ())


class PublicationResultsMixin:
    def finish_publication(
        self,
        job_id,
        claim_token,
        website_receipt=None,
        email_summary=None,
        state="queued",
        error=None,
    ):
        """Finalize a claimed publication and release its lease."""
        if state not in _FINAL_STATES:
            raise InvalidInput("invalid publication result state")
        now = utcnow()
        with self.engine.begin() as conn:
            job = conn.execute(
                select(publications)
                .where(
                    publications.c.id == job_id,
                    publications.c.claim_token == claim_token,
                    publications.c.state == "claimed",
                )
                .with_for_update()
            ).mappings().first()
            if job is None:
                raise Conflict("publication claim is no longer held")
            channels = job["channels"] or ()
            if state == "published":
                if _channel(channels, "website"):
                    expected = "withdrawn" if job["action"] == "withdraw" else "delivered"
                    if not isinstance(website_receipt, dict):
                        raise InvalidInput("website receipt is required")
                    if website_receipt.get("status") != expected or not website_receipt.get("url"):
                        raise InvalidInput("website receipt is incomplete")
                if _channel(channels, "email"):
                    if not isinstance(email_summary, dict):
                        raise InvalidInput("email summary is required")
                    if any(email_summary.get(k) != 0 for k in ("remaining", "failed", "uncertain")):
                        raise InvalidInput("email summary is not complete")
            changed = conn.execute(
                update(publications)
                .where(
                    publications.c.id == job_id,
                    publications.c.claim_token == claim_token,
                    publications.c.state == "claimed",
                )
                .values(
                    state=state,
                    website_receipt=website_receipt,
                    email_summary=email_summary,
                    claim_token=None,
                    lease_until=None,
                    last_error=SANITIZED_ERROR if error else None,
                    updated_at=now,
                )
            )
            if changed.rowcount != 1:
                raise Conflict("publication claim changed")
            if state == "published":
                article_state = "withdrawn" if job["action"] == "withdraw" else "published"
                conn.execute(
                    update(articles)
                    .where(
                        articles.c.id == job["article_id"],
                        articles.c.current_revision == job["revision"],
                    )
                    .values(state=article_state, updated_at=now)
                )
                _event(
                    conn,
                    article_id=job["article_id"],
                    revision=job["revision"],
                    kind="publication_finished",
                    actor=job["actor"],
                    payload={"publication_id": job_id, "action": job["action"], "state": state},
                    created_at=now,
                )
