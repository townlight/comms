"""Schedule publish, correction and withdrawal jobs with durable audit state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .models import PUBLICATION_ACTIONS, approvals, articles, publications
from .records_guard import is_publication_exposed
from .store import Conflict, InvalidInput, canonical_bytes, new_id, utcnow

_ALLOWED_CHANNELS = ("website", "email")


def _utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class SchedulingMixin:
    def schedule(self, article_id, expected_revision, expected_sha256, actor,
                 due_at, channels, action="publish"):
        if not isinstance(actor, str) or not actor or len(actor) > 128:
            raise InvalidInput("actor")
        if (isinstance(expected_revision, bool)
                or not isinstance(expected_revision, int) or expected_revision < 1):
            raise InvalidInput("expected_revision")
        if not isinstance(expected_sha256, str) or not expected_sha256:
            raise InvalidInput("expected_sha256")
        if action not in PUBLICATION_ACTIONS:
            raise InvalidInput("action")
        if (not isinstance(due_at, datetime)
                or due_at.tzinfo is None or due_at.utcoffset() is None):
            raise InvalidInput("due_at")
        due_at = _utc(due_at)
        if isinstance(channels, str) or not isinstance(channels, (list, tuple)) or not channels:
            raise InvalidInput("channels")
        channels = list(channels)
        if len(set(channels)) != len(channels) or any(c not in _ALLOWED_CHANNELS for c in channels):
            raise InvalidInput("channels")

        now = utcnow()
        if due_at > now + timedelta(days=365):
            raise InvalidInput("due_at")
        with self.engine.begin() as conn:
            art = self._article(conn, article_id, expected_revision, lock=True)
            rev = self._revision(conn, article_id, expected_revision)
            if rev["sha256"] != expected_sha256:
                raise Conflict("sha256")
            claimed = conn.execute(select(publications.c.id).where(
                publications.c.article_id == article_id,
                publications.c.state == "claimed",
            ).limit(1)).first()
            if claimed is not None:
                raise Conflict("claimed publication")

            duplicate = False
            existing = conn.execute(select(publications).where(
                publications.c.article_id == article_id,
                publications.c.revision == expected_revision,
                publications.c.action == action,
            )).mappings().first()
            if existing is not None:
                if (existing["actor"] == actor
                        and canonical_bytes(existing["channels"]) == canonical_bytes(channels)
                        and _utc(existing["due_at"]) == due_at):
                    duplicate = True
                else:
                    raise Conflict("publication exists")

            if not duplicate:
                if action in ("correct", "withdraw"):
                    self._require_published_archives(conn, article_id)
                    prior_rows = conn.execute(select(publications).where(
                        publications.c.article_id == article_id,
                        publications.c.action.in_(("publish", "correct")),
                    ).order_by(publications.c.created_at, publications.c.id).limit(1001)).mappings().all()
                    prior = next((row for row in prior_rows if is_publication_exposed(row)), None)
                    if prior is None:
                        raise Conflict("prior publication")

                publication_id = new_id()
                if action in ("publish", "correct"):
                    if art["state"] != "approved":
                        raise Conflict("state")
                    approved_sha = conn.execute(select(approvals.c.sha256).where(
                        approvals.c.article_id == article_id,
                        approvals.c.revision == expected_revision,
                    )).scalar_one_or_none()
                    if approved_sha is None or approved_sha != expected_sha256:
                        raise Conflict("approval")
                    update = articles.update().where(
                        articles.c.id == article_id,
                        articles.c.current_revision == expected_revision,
                        articles.c.state == "approved",
                    ).values(state="scheduled", updated_at=now)
                else:
                    update = articles.update().where(
                        articles.c.id == article_id,
                        articles.c.current_revision == expected_revision,
                        articles.c.state != "withdrawn",
                    ).values(state="scheduled", updated_at=now)
                if conn.execute(update).rowcount != 1:
                    raise Conflict("state")

                conn.execute(publications.update().where(
                    publications.c.article_id == article_id,
                    publications.c.state == "pending",
                    publications.c.id != publication_id,
                ).values(state="failed", last_error="superseded", updated_at=now))
                conn.execute(publications.insert().values(
                    id=publication_id, article_id=article_id, revision=expected_revision,
                    action=action, channels=channels, due_at=due_at, state="pending",
                    attempts=0, actor=actor, created_at=now, updated_at=now,
                ))
                self._event(conn, article_id, expected_revision, "scheduled", actor, {
                    "action": action, "publication_id": publication_id,
                    "sha256": expected_sha256, "channels": channels,
                    "due_at": due_at.isoformat(),
                })
        return self.get(article_id)
