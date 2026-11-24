"""Editorial workflow: create, edit and review only."""

from __future__ import annotations

from sqlalchemy import select

from .models import ARTICLE_KINDS, articles, publications, revisions
from .store import Conflict, InvalidInput, new_id, utcnow


class EditorialMixin:
    """Durable append-only editorial operations with audit events."""

    def _require_actor(self, actor):
        return self._bounded(actor, "actor", 128)

    def _require_expected(self, value, name="expected_revision"):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise InvalidInput(name)
        return value

    def create(self, actor, kind, title, body, language, sources,
               access_review=None, translation_provenance=None):
        actor = self._require_actor(actor)
        if kind not in ARTICLE_KINDS:
            raise InvalidInput("kind")
        content = self._content(title, body, language, sources,
                                access_review, translation_provenance)
        article_id, now = new_id(), utcnow()
        with self.engine.begin() as conn:
            conn.execute(articles.insert().values(
                id=article_id, title=content["title"], kind=kind,
                current_revision=1, state="draft",
                created_at=now, updated_at=now))
            self._insert_revision(conn, article_id, 1, content, actor, now)
            self._event(conn, article_id, 1, "created", actor,
                        {"kind": kind, "sha256": content["sha256"]})
        return self.get(article_id)

    def edit(self, article_id, actor, expected_revision, title, body, language,
             sources, access_review=None, translation_provenance=None):
        actor = self._require_actor(actor)
        expected_revision = self._require_expected(expected_revision)
        content = self._content(title, body, language, sources,
                                access_review, translation_provenance)
        now, next_revision = utcnow(), expected_revision + 1
        with self.engine.begin() as conn:
            self._article(conn, article_id, expected_revision, lock=True)
            self._require_published_archives(conn, article_id)
            old = self._revision(conn, article_id, expected_revision)
            content = self._content(
                title, body, language, sources, access_review,
                translation_provenance,
                attachment_refs=old["attachment_refs"])
            claimed = conn.execute(
                select(publications.c.id).where(
                    publications.c.article_id == article_id,
                    publications.c.state == "claimed")).first()
            if claimed is not None:
                raise Conflict("ongoing publication")
            result = conn.execute(
                articles.update().where(
                    articles.c.id == article_id,
                    articles.c.current_revision == expected_revision).values(
                    title=content["title"], current_revision=next_revision,
                    state="draft", updated_at=now))
            if result.rowcount != 1:
                raise Conflict("revision mismatch")
            self._insert_revision(conn, article_id, next_revision, content, actor, now)
            conn.execute(publications.update().where(
                publications.c.article_id == article_id,
                publications.c.revision == expected_revision,
                publications.c.state == "pending").values(
                state="failed", last_error="superseded", updated_at=now))
            self._event(conn, article_id, next_revision, "edited", actor,
                        {"sha256": content["sha256"],
                         "superseded_revision": expected_revision})
        return self.get(article_id)

    def review(self, article_id, actor, expected_revision, note):
        actor = self._require_actor(actor)
        expected_revision = self._require_expected(expected_revision)
        note = self._bounded(note, "note", 2000)
        now = utcnow()
        with self.engine.begin() as conn:
            article = self._article(conn, article_id, expected_revision, lock=True)
            if article["state"] not in ("draft", "review"):
                raise Conflict("state")
            result = conn.execute(
                articles.update().where(
                    articles.c.id == article_id,
                    articles.c.current_revision == expected_revision,
                    articles.c.state.in_(("draft", "review"))).values(
                    state="review", updated_at=now))
            if result.rowcount != 1:
                raise Conflict("state")
            self._event(conn, article_id, expected_revision, "reviewed", actor,
                        {"note": note})
        return self.get(article_id)

    @staticmethod
    def _insert_revision(conn, article_id, revision, content, actor, now):
        conn.execute(revisions.insert().values(
            article_id=article_id, revision=revision, title=content["title"],
            body=content["body"], language=content["language"],
            source_refs=content["source_refs"],
            attachment_refs=content.get("attachment_refs", []),
            access_review=content["access_review"],
            translation_provenance=content["translation_provenance"],
            sha256=content["sha256"], actor=actor, created_at=now))
