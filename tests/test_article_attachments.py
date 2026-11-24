"""Real attachment workflow tests against SQLite-backed repository state."""
import hashlib
import os
import tempfile
import unittest

from sqlalchemy import create_engine, func, select, update

from townlight_comms import migrations
from townlight_comms.models import approvals, attachments, revisions
from townlight_comms.store import Conflict, InvalidInput, NotFound
from townlight_comms.workflow import CommunicationsRepository


class ArticleAttachmentTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine("sqlite:///" + self.path)
        migrations.upgrade(self.engine)
        self.repo = CommunicationsRepository(self.engine)
        self.sources = [{
            "kind": "meetings", "id": "m1", "revision": "1",
            "title": "Council", "url": "/public/meetings/m1",
            "sha256": "a" * 64, "eligible": True,
        }]
        self.access = {"reviewed": True, "findings": []}

    def tearDown(self):
        self.engine.dispose()
        os.unlink(self.path)

    def _sha(self, article_id, revision):
        with self.repo.engine.connect() as conn:
            return conn.execute(select(revisions.c.sha256).where(
                revisions.c.article_id == article_id,
                revisions.c.revision == revision)).scalar_one()

    def _refs(self, article_id, revision):
        with self.repo.engine.connect() as conn:
            return conn.execute(select(revisions.c.attachment_refs).where(
                revisions.c.article_id == article_id,
                revisions.c.revision == revision)).scalar_one()

    def _count_revisions(self, article_id):
        with self.repo.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(revisions).where(
                revisions.c.article_id == article_id)).scalar_one()

    def _create(self):
        return self.repo.create(
            "clerk", "announcement", "Old", "Old body", "en", self.sources,
            access_review=self.access)["id"]

    def test_public_attachment_revisions_and_exact_reads(self):
        article_id = self._create()
        first_sha = self._sha(article_id, 1)
        actual = b"public minutes\n\x00\xff"
        digest = hashlib.sha256(actual).hexdigest()
        self.repo.add_attachment(
            article_id, 1, "clerk", "minutes.bin", "application/octet-stream",
            actual, public=True)
        self.assertNotEqual(self._sha(article_id, 2), first_sha)
        refs = self._refs(article_id, 2)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["sha256"], digest)
        self.assertEqual(refs[0]["size"], len(actual))
        attachment_id = refs[0]["id"]

        self.repo.edit(
            article_id, "clerk", 2, "New title", "New body", "en",
            self.sources, access_review=self.access)
        self.assertEqual(self._refs(article_id, 3), refs)
        stored = self.repo.read_attachment(article_id, attachment_id)
        self.assertEqual(stored["content"], actual)
        self.assertEqual(stored["sha256"], digest)
        self.assertEqual(stored["size"], len(actual))
        self.assertEqual(stored["name"], "minutes.bin")
        self.assertEqual(stored["media_type"], "application/octet-stream")

        self.repo.remove_attachment(article_id, 3, "clerk", attachment_id)
        self.assertTrue(self._sha(article_id, 4))
        with self.assertRaises(NotFound):
            self.repo.read_attachment(article_id, attachment_id)
        old = self.repo.read_attachment(article_id, attachment_id, revision=2)
        self.assertEqual(old["content"], actual)
        self.assertEqual(old["sha256"], digest)

    def test_private_attachment_blocks_approval_and_old_approval_survives(self):
        article_id = self._create()
        self.repo.add_attachment(
            article_id, 1, "clerk", "secret.txt", "text/plain",
            b"private", public=False)
        with self.repo.engine.connect() as conn:
            private_id = conn.execute(select(attachments.c.id).where(
                attachments.c.article_id == article_id,
                attachments.c.revision == 2)).scalar_one()
        self.repo.review(article_id, "clerk", 2, "checked")
        with self.assertRaises(Conflict):
            self.repo.approve(
                article_id, 2, self._sha(article_id, 2), "clerk", self.sources)

        self.repo.remove_attachment(article_id, 2, "clerk", private_id)
        self.repo.review(article_id, "clerk", 3, "checked")
        approved_sha = self._sha(article_id, 3)
        self.repo.approve(article_id, 3, approved_sha, "clerk", self.sources)
        with self.repo.engine.connect() as conn:
            self.assertEqual(conn.execute(select(approvals.c.sha256).where(
                approvals.c.article_id == article_id,
                approvals.c.revision == 3)).scalar_one(), approved_sha)

        self.repo.add_attachment(
            article_id, 3, "clerk", "public.txt", "text/plain",
            b"public", public=True)
        with self.repo.engine.connect() as conn:
            preserved = conn.execute(select(approvals.c.sha256).where(
                approvals.c.article_id == article_id,
                approvals.c.revision == 3)).scalar_one()
        self.assertEqual(preserved, approved_sha)
        with self.assertRaises(Conflict):
            self.repo.approve(
                article_id, 4, self._sha(article_id, 4), "clerk", self.sources)

    def test_invalid_attachment_input_and_tamper_detection(self):
        article_id = self._create()
        first_sha = self._sha(article_id, 1)
        with self.assertRaises(InvalidInput):
            self.repo.add_attachment(
                article_id, 1, "clerk", "../x.txt", "text/plain",
                b"x", public=True)
        with self.assertRaises(InvalidInput):
            self.repo.add_attachment(
                article_id, 1, "clerk", "large.bin", "application/octet-stream",
                b"x" * (10 * 1024 * 1024 + 1), public=True)
        self.assertEqual(self._count_revisions(article_id), 1)
        self.assertEqual(self._sha(article_id, 1), first_sha)

        actual = b"tamper target"
        self.repo.add_attachment(
            article_id, 1, "clerk", "target.bin", "application/octet-stream",
            actual, public=True)
        with self.repo.engine.connect() as conn:
            attachment_id = conn.execute(select(attachments.c.id).where(
                attachments.c.article_id == article_id,
                attachments.c.revision == 2)).scalar_one()
        with self.repo.engine.begin() as conn:
            conn.execute(update(attachments).where(
                attachments.c.id == attachment_id).values(content=b"tampered"))
        with self.assertRaises(Conflict):
            self.repo.read_attachment(article_id, attachment_id)
        self.repo.review(article_id, "clerk", 2, "checked")
        with self.assertRaises(Conflict):
            self.repo.approve(
                article_id, 2, self._sha(article_id, 2), "clerk", self.sources)


if __name__ == "__main__":
    unittest.main()
