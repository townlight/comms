from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select, update

from .models import publications, records_receipts
from .store import Conflict, InvalidInput, utcnow

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PUBLISHED_ACTIONS = ("publish", "correct")
_EXPOSED_ROWS_LIMIT = 1000


def is_publication_exposed(row):
    if row is None:
        return False
    try:
        action = row["action"]
        state = row["state"]
        website = row["website_receipt"]
        email = row["email_summary"]
    except (KeyError, TypeError):
        return False
    if action not in _PUBLISHED_ACTIONS:
        return False
    if state == "published":
        return True
    if isinstance(website, dict) and website.get("status") == "delivered":
        return True
    if isinstance(email, dict):
        sent = email.get("sent")
        if isinstance(sent, int) and not isinstance(sent, bool) and sent > 0:
            return True
    return False


def _uuid(value, name):
    if not isinstance(value, str):
        raise InvalidInput(name)
    try:
        return str(UUID(value))
    except (ValueError, AttributeError):
        raise InvalidInput(name) from None


def _text(value, name, maximum):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise InvalidInput(name)
    return value


def _revision(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise InvalidInput("revision")
    return value


class RecordsGuardMixin:
    def _require_published_archives(self, conn, article_id):
        exposed_rows = conn.execute(
            select(publications)
            .where(
                publications.c.article_id == article_id,
                publications.c.action.in_(_PUBLISHED_ACTIONS),
            )
            .order_by(publications.c.revision, publications.c.created_at, publications.c.id)
            .limit(_EXPOSED_ROWS_LIMIT + 1)
        ).mappings().all()
        if len(exposed_rows) > _EXPOSED_ROWS_LIMIT:
            raise Conflict("publication_receipts")
        exposed_revisions = []
        for row in exposed_rows:
            if is_publication_exposed(row) and row["revision"] not in exposed_revisions:
                exposed_revisions.append(row["revision"])
        for revision in exposed_revisions:
            receipt = conn.execute(
                select(records_receipts.c.article_id)
                .where(
                    records_receipts.c.article_id == article_id,
                    records_receipts.c.revision == revision,
                )
                .limit(1)
            ).first()
            if receipt is None:
                raise Conflict(
                    f"archive published revision {revision} in Records "
                    "before changes/withdrawal"
                )

    def record_records_receipt(
        self, article_id, revision, archive_sha256, records_id, file_id
    ):
        article_id = _uuid(article_id, "article_id")
        revision = _revision(revision)
        if not isinstance(archive_sha256, str) or not _HEX64.fullmatch(archive_sha256):
            raise InvalidInput("archive_sha256")
        records_id = _text(records_id, "records_id", 128)
        file_id = _uuid(file_id, "file_id")

        with self.engine.begin() as conn:
            self._article(conn, article_id, lock=True)
            publication_rows = conn.execute(
                select(publications)
                .where(
                    publications.c.article_id == article_id,
                    publications.c.revision == revision,
                    publications.c.action.in_(_PUBLISHED_ACTIONS),
                )
            ).mappings().all()
            if not any(is_publication_exposed(row) for row in publication_rows):
                raise Conflict("revision is not published")

            existing = conn.execute(
                select(records_receipts).where(
                    records_receipts.c.article_id == article_id,
                    records_receipts.c.revision == revision,
                )
            ).mappings().first()
            now = utcnow()
            fields = {
                "archive_sha256": archive_sha256,
                "records_id": records_id,
                "file_id": file_id,
            }
            if existing is None:
                conn.execute(
                    records_receipts.insert().values(
                        article_id=article_id,
                        revision=revision,
                        archive_sha256=archive_sha256,
                        records_id=records_id,
                        file_id=file_id,
                        created_at=now,
                    )
                )
                return {
                    "article_id": article_id,
                    "revision": revision,
                    **fields,
                    "created_at": now,
                }

            current = dict(existing)
            if all(current[key] == value for key, value in fields.items()):
                return current
            conn.execute(
                update(records_receipts)
                .where(
                    records_receipts.c.article_id == article_id,
                    records_receipts.c.revision == revision,
                )
                .values(**fields, created_at=now)
            )
            current.update(fields)
            current["created_at"] = now
            return current
