"""Immutable public source snapshots captured at approval time."""

from __future__ import annotations

import copy

from sqlalchemy import select

from .models import approvals, approved_source_snapshots
from .store import Conflict, InvalidInput, canonical_bytes, utcnow

_HEX = set("0123456789abcdef")


class SourceSnapshotMixin:
    def _snapshot_identity(self, article_id, revision, expected_sha256):
        article_id = self._bounded(article_id, "article_id", 256)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise InvalidInput("revision")
        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(ch not in _HEX for ch in expected_sha256)
        ):
            raise InvalidInput("expected_sha256")
        return article_id, revision, expected_sha256

    @staticmethod
    def _approved(conn, article_id, revision, expected_sha256):
        return conn.execute(
            select(approvals.c.article_id).where(
                approvals.c.article_id == article_id,
                approvals.c.revision == revision,
                approvals.c.sha256 == expected_sha256,
            )
        ).first() is not None

    def capture_source_snapshots(
        self, article_id, revision, expected_sha256, snapshots
    ):
        article_id, revision, expected_sha256 = self._snapshot_identity(
            article_id, revision, expected_sha256
        )
        # Validates approval, public source digests, and public attachments.
        self.build_public_artifact(
            article_id, revision, expected_sha256, snapshots
        )
        with self.engine.begin() as conn:
            self._article(
                conn, article_id, expected_revision=revision, lock=True
            )
            if not self._approved(conn, article_id, revision, expected_sha256):
                raise Conflict("approval")
            existing = conn.execute(
                select(approved_source_snapshots).where(
                    approved_source_snapshots.c.article_id == article_id,
                    approved_source_snapshots.c.revision == revision,
                )
            ).mappings().first()
            if existing is not None:
                if (
                    existing["revision_sha256"] != expected_sha256
                    or canonical_bytes(existing["snapshots"])
                    != canonical_bytes(snapshots)
                ):
                    raise Conflict("source_snapshots")
                return True
            if not self._approved(conn, article_id, revision, expected_sha256):
                raise Conflict("approval")
            conn.execute(
                approved_source_snapshots.insert().values(
                    article_id=article_id,
                    revision=revision,
                    revision_sha256=expected_sha256,
                    snapshots=snapshots,
                    created_at=utcnow(),
                )
            )
        return True

    def get_source_snapshots(self, article_id, revision, expected_sha256):
        article_id, revision, expected_sha256 = self._snapshot_identity(
            article_id, revision, expected_sha256
        )
        with self.engine.connect() as conn:
            if not self._approved(conn, article_id, revision, expected_sha256):
                raise Conflict("approval")
            row = conn.execute(
                select(approved_source_snapshots.c.snapshots).where(
                    approved_source_snapshots.c.article_id == article_id,
                    approved_source_snapshots.c.revision == revision,
                    approved_source_snapshots.c.revision_sha256 == expected_sha256,
                )
            ).mappings().first()
            if row is None:
                raise Conflict("source_snapshots")
            return copy.deepcopy(row["snapshots"])
