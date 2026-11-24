"""Attachment workflows built on immutable article revisions."""

from __future__ import annotations

import hashlib

from sqlalchemy import select, update

from .models import articles, attachments, publications
from .store import Conflict, InvalidInput, NotFound, new_id, utcnow

_MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024


class AttachmentMixin:
    """Add/remove attachment references and read verified stored bytes."""

    def _bump(self, conn, article_id, expected_revision, actor, newrefs):
        """Publish a new draft revision carrying ``newrefs`` as its manifest."""
        self._article(conn, article_id, expected_revision, lock=True)
        self._require_published_archives(conn, article_id)
        old = self._revision(conn, article_id, expected_revision)
        content = self._content(
            old["title"],
            old["body"],
            old["language"],
            old["source_refs"],
            old["access_review"],
            old["translation_provenance"],
            attachment_refs=newrefs,
        )
        claimed = conn.execute(
            select(publications.c.id).where(
                publications.c.article_id == article_id,
                publications.c.state == "claimed",
            )
        ).first()
        if claimed is not None:
            raise Conflict("ongoing publication")
        now = utcnow()
        next_revision = expected_revision + 1
        result = conn.execute(
            update(articles)
            .where(
                articles.c.id == article_id,
                articles.c.current_revision == expected_revision,
            )
            .values(
                title=content["title"],
                current_revision=next_revision,
                state="draft",
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            raise Conflict("revision mismatch")
        self._insert_revision(conn, article_id, next_revision, content, actor, now)
        conn.execute(
            update(publications)
            .where(
                publications.c.article_id == article_id,
                publications.c.revision == expected_revision,
                publications.c.state == "pending",
            )
            .values(state="failed", last_error="superseded", updated_at=now)
        )
        return content, next_revision, now

    def add_attachment(
        self,
        article_id,
        expected_revision,
        actor,
        name,
        media_type,
        content,
        public=False,
    ):
        actor = self._require_actor(actor)
        expected_revision = self._require_expected(expected_revision)
        name = self._bounded(name, "name", 150)
        media_type = self._bounded(media_type, "media_type", 128)
        if not isinstance(content, bytes):
            raise InvalidInput("content")
        size = len(content)
        if not 1 <= size <= _MAX_ATTACHMENT_SIZE:
            raise InvalidInput("content")
        if not isinstance(public, bool):
            raise InvalidInput("public")
        attachment_id = new_id()
        sha256 = hashlib.sha256(content).hexdigest()
        ref = {
            "id": attachment_id,
            "name": name,
            "media_type": media_type,
            "size": size,
            "sha256": sha256,
            "public": public,
        }
        with self.engine.begin() as conn:
            old = self._revision(conn, article_id, expected_revision)
            refs = list(old["attachment_refs"]) + [ref]
            # _bump validates the complete 20-file / 20 MiB manifest.
            _, next_revision, now = self._bump(
                conn, article_id, expected_revision, actor, refs
            )
            conn.execute(
                attachments.insert().values(
                    id=attachment_id,
                    article_id=article_id,
                    revision=next_revision,
                    name=name,
                    media_type=media_type,
                    content=content,
                    sha256=sha256,
                    size=size,
                    public=public,
                    created_at=now,
                )
            )
            self._event(
                conn,
                article_id,
                next_revision,
                "attachment_added",
                actor,
                {"attachment_id": attachment_id, "sha256": sha256, "size": size},
            )
        return self.get(article_id)

    def remove_attachment(
        self, article_id, expected_revision, actor, attachment_id
    ):
        actor = self._require_actor(actor)
        expected_revision = self._require_expected(expected_revision)
        with self.engine.begin() as conn:
            old = self._revision(conn, article_id, expected_revision)
            refs = []
            found = False
            for ref in old["attachment_refs"]:
                if ref.get("id") == attachment_id:
                    found = True
                else:
                    refs.append(ref)
            if not found:
                raise NotFound(attachment_id)
            _, next_revision, _ = self._bump(
                conn, article_id, expected_revision, actor, refs
            )
            self._event(
                conn,
                article_id,
                next_revision,
                "attachment_removed",
                actor,
                {"attachment_id": attachment_id},
            )
        return self.get(article_id)

    def read_attachment(self, article_id, attachment_id, revision=None):
        if revision is not None:
            revision = self._require_expected(revision, "revision")
        with self.engine.connect() as conn:
            article = self._article(conn, article_id)
            if revision is None:
                revision = article.get("current_revision")
                if revision is None:
                    revision = article.get("revision")
            current = self._revision(conn, article_id, revision)
            ref = next(
                (
                    item
                    for item in current["attachment_refs"]
                    if item.get("id") == attachment_id
                ),
                None,
            )
            if ref is None:
                raise NotFound(attachment_id)
            row = conn.execute(
                select(attachments).where(
                    attachments.c.id == attachment_id,
                    attachments.c.article_id == article_id,
                )
            ).mappings().first()
            if row is None:
                raise NotFound(attachment_id)
            data = row["content"]
            actual = hashlib.sha256(data).hexdigest()
            if (
                len(data) != ref["size"]
                or actual != ref["sha256"]
                or row["sha256"] != ref["sha256"]
                or row["size"] != ref["size"]
                or row["name"] != ref["name"]
                or row["media_type"] != ref["media_type"]
            ):
                raise Conflict("attachment mismatch")
            return {
                "content": data,
                "name": row["name"],
                "media_type": row["media_type"],
                "sha256": row["sha256"],
                "size": row["size"],
            }
