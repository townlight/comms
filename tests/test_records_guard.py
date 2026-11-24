"""Real SQLite tests for the Records guard around published revisions."""

import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine, select, update

from townlight_comms import migrations
from townlight_comms.models import articles, publications, records_receipts, revisions
from townlight_comms.store import Conflict
from townlight_comms.workflow import CommunicationsRepository


class RecordsGuardTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.base_engine = create_engine("sqlite:///" + self.path)
        migrations.upgrade(self.base_engine)
        self.repo = CommunicationsRepository(self.base_engine)
        self.engine = self.repo.engine
        self.sources = [{
            "kind": "meetings", "id": "m1", "revision": "1",
            "title": "Council", "url": "/public/meetings/m1",
            "sha256": "a" * 64, "eligible": True,
        }]
        self.access = {"reviewed": True, "findings": []}

    def tearDown(self):
        self.base_engine.dispose()
        os.unlink(self.path)

    def _sha(self, article_id, revision):
        with self.engine.connect() as conn:
            return conn.execute(select(revisions.c.sha256).where(
                revisions.c.article_id == article_id,
                revisions.c.revision == revision)).scalar_one()

    def _approved_due(self):
        article_id = self.repo.create(
            "clerk", "announcement", "Notice", "Body", "en", self.sources,
            access_review=self.access)["id"]
        self.repo.review(article_id, "clerk", 1, "checked")
        sha = self._sha(article_id, 1)
        self.repo.approve(article_id, 1, sha, "clerk", self.sources)
        due = datetime.now(UTC) - timedelta(seconds=1)
        self.repo.schedule(article_id, 1, sha, "clerk", due, ["website"])
        return article_id

    def _publish_ack(self, article_id, revision):
        # Delivery acknowledgement fixture: direct DB state only, no network.
        now = datetime.now(UTC)
        with self.engine.begin() as conn:
            result = conn.execute(update(publications).where(
                publications.c.article_id == article_id,
                publications.c.revision == revision,
                publications.c.action == "publish",
            ).values(state="published", website_receipt={"acknowledged": True},
                     updated_at=now))
            self.assertEqual(result.rowcount, 1)
            result = conn.execute(update(articles).where(
                articles.c.id == article_id,
            ).values(state="published", updated_at=now))
            self.assertEqual(result.rowcount, 1)

    def test_receipt_unblocks_edit_and_withdraw_schedule(self):
        article_id = self._approved_due()
        self._publish_ack(article_id, 1)
        sha1 = self._sha(article_id, 1)
        due = datetime.now(UTC) + timedelta(minutes=5)

        with self.assertRaises(Conflict):
            self.repo.edit(article_id, "clerk", 1, "Notice", "Edited", "en",
                           self.sources, access_review=self.access)
        with self.assertRaises(Conflict):
            self.repo.add_attachment(article_id, 1, "clerk", "file.txt",
                                     "text/plain", b"data")
        with self.assertRaises(Conflict):
            self.repo.schedule(article_id, 1, sha1, "clerk", due,
                               ["website"], action="withdraw")

        archive_sha = "b" * 64
        records_id = "records-" + str(uuid4())
        file_id = str(uuid4())
        first = self.repo.record_records_receipt(
            article_id, 1, archive_sha, records_id, file_id)
        self.assertEqual(first["file_id"], file_id)
        second = self.repo.record_records_receipt(
            article_id, 1, archive_sha, records_id, file_id)
        self.assertEqual(second["archive_sha256"], archive_sha)
        self.assertEqual(second["records_id"], records_id)
        self.assertEqual(second["file_id"], file_id)
        with self.engine.connect() as conn:
            rows = conn.execute(select(records_receipts.c.article_id).where(
                records_receipts.c.article_id == article_id,
                records_receipts.c.revision == 1)).all()
        self.assertEqual(len(rows), 1)

        edited = self.repo.edit(
            article_id, "clerk", 1, "Notice", "Edited Body", "en",
            self.sources, access_review=self.access)
        self.assertEqual(edited["current_revision"], 2)
        self.assertEqual(edited["state"], "draft")
        with self.engine.connect() as conn:
            original_body = conn.execute(select(revisions.c.body).where(
                revisions.c.article_id == article_id,
                revisions.c.revision == 1)).scalar_one()
        self.assertEqual(original_body, "Body")

        with self.assertRaises(Conflict):
            self.repo.record_records_receipt(
                article_id, 2, "c" * 64, "records-2", str(uuid4()))

        sha2 = self._sha(article_id, 2)
        self.repo.review(article_id, "clerk", 2, "withdraw review")
        self.repo.approve(article_id, 2, sha2, "clerk", self.sources)
        scheduled = self.repo.schedule(
            article_id, 2, sha2, "clerk", due, ["website"], action="withdraw")
        self.assertEqual(scheduled["state"], "scheduled")

    def test_correction_rejects_queued_only_prior(self):
        article_id = self._approved_due()
        now = datetime.now(UTC)
        with self.engine.begin() as conn:
            result = conn.execute(update(publications).where(
                publications.c.article_id == article_id,
                publications.c.revision == 1,
                publications.c.action == "publish",
            ).values(state="queued", updated_at=now))
            self.assertEqual(result.rowcount, 1)
        with self.assertRaises(Conflict):
            self.repo.schedule(
                article_id, 1, self._sha(article_id, 1), "clerk",
                now + timedelta(minutes=5), ["website"], action="correct")


if __name__ == "__main__":
    unittest.main()
