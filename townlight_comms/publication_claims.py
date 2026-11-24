from datetime import timedelta

from sqlalchemy import or_, select, update

from .models import approvals, articles, publications
from .store import InvalidInput, new_id, utcnow


class PublicationClaimsMixin:
    def claim_due(self, now=None, lease_seconds=120):
        if not 30 <= lease_seconds <= 600:
            raise InvalidInput("lease_seconds must be between 30 and 600")
        now = utcnow() if now is None else now
        lease_error = "claim lease expired; inspect before retry"

        with self.engine.begin() as conn:
            conn.execute(
                update(publications)
                .where(
                    publications.c.state == "claimed",
                    publications.c.lease_until <= now,
                )
                .values(
                    state="uncertain",
                    last_error=lease_error,
                    updated_at=now,
                )
            )

            job_row = conn.execute(
                select(publications)
                .where(
                    publications.c.state == "pending",
                    publications.c.due_at <= now,
                    or_(
                        publications.c.next_attempt_at.is_(None),
                        publications.c.next_attempt_at <= now,
                    ),
                )
                .order_by(publications.c.due_at, publications.c.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).mappings().first()

            if job_row is None:
                return None

            job = dict(job_row)
            article_row = conn.execute(
                select(articles.c.current_revision)
                .where(articles.c.id == job["article_id"])
            ).mappings().first()

            if article_row is None or article_row["current_revision"] != job["revision"]:
                conn.execute(
                    update(publications)
                    .where(
                        publications.c.id == job["id"],
                        publications.c.state == "pending",
                    )
                    .values(
                        state="failed",
                        last_error="source changed",
                        updated_at=now,
                    )
                )
                return None

            revision = self._revision(conn, job["article_id"], job["revision"])

            if job["action"] != "withdraw":
                approved_row = conn.execute(
                    select(approvals.c.sha256)
                    .where(
                        approvals.c.article_id == job["article_id"],
                        approvals.c.revision == job["revision"],
                    )
                    .limit(1)
                ).mappings().first()

                if (
                    approved_row is None
                    or revision is None
                    or approved_row["sha256"] != revision["sha256"]
                ):
                    conn.execute(
                        update(publications)
                        .where(
                            publications.c.id == job["id"],
                            publications.c.state == "pending",
                        )
                        .values(
                            state="failed",
                            last_error="source changed",
                            updated_at=now,
                        )
                    )
                    return None

            claim_token = new_id()
            lease_until = now + timedelta(seconds=lease_seconds)
            result = conn.execute(
                update(publications)
                .where(
                    publications.c.id == job["id"],
                    publications.c.state == "pending",
                )
                .values(
                    state="claimed",
                    claim_token=claim_token,
                    lease_until=lease_until,
                    attempts=publications.c.attempts + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                return None

            job.update(
                state="claimed",
                claim_token=claim_token,
                lease_until=lease_until,
                attempts=job["attempts"] + 1,
                updated_at=now,
            )
            job["content"] = revision
            return job