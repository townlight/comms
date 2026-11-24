"""Real workflow tests for editorial, approval and scheduling."""
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select

from townlight_comms import migrations
from townlight_comms.models import approvals, events, publications, revisions
from townlight_comms.store import Conflict
from townlight_comms.workflow import CommunicationsRepository


class WorkflowTests(unittest.TestCase):
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

    def _create(self, title="Old", body="Old body"):
        return self.repo.create(
            "clerk", "announcement", title, body, "en", self.sources,
            access_review=self.access)["id"]

    def test_restart_edit_keeps_old_revision_and_stale_edit_conflicts(self):
        article_id = self._create()
        restarted = CommunicationsRepository(self.engine)
        restarted.edit(article_id, "clerk", 1, "New", "New body", "en", self.sources,
                       access_review=self.access)
        with self.repo.engine.connect() as conn:
            old = conn.execute(select(revisions.c.title, revisions.c.body).where(
                revisions.c.article_id == article_id,
                revisions.c.revision == 1)).one()
        self.assertEqual(old.title, "Old")
        self.assertEqual(old.body, "Old body")
        with self.assertRaises(Conflict):
            restarted.edit(article_id, "clerk", 1, "Again", "Again", "en", self.sources,
                           access_review=self.access)

    def test_changed_source_conflicts_before_exact_approval_succeeds(self):
        article_id = self._create()
        self.repo.review(article_id, "clerk", 1, "checked")
        approved_sha = self._sha(article_id, 1)
        changed = [dict(self.sources[0], url="/public/meetings/m2")]
        with self.assertRaises(Conflict):
            self.repo.approve(article_id, 1, approved_sha, "clerk", changed)
        self.repo.approve(article_id, 1, approved_sha, "clerk", self.sources)
        self.assertEqual(self.repo.get(article_id)["state"], "approved")

    def test_schedule_duplicate_edit_cancels_pending_and_blocks_unreviewed(self):
        article_id = self._create()
        self.repo.review(article_id, "clerk", 1, "checked")
        approved_sha = self._sha(article_id, 1)
        self.repo.approve(article_id, 1, approved_sha, "clerk", self.sources)
        due = datetime.now(UTC) + timedelta(minutes=5)
        self.repo.schedule(article_id, 1, approved_sha, "clerk", due, ["website"])
        self.repo.schedule(article_id, 1, approved_sha, "clerk", due, ["website"])
        with self.repo.engine.connect() as conn:
            pub_count = conn.execute(select(publications.c.id).where(
                publications.c.article_id == article_id,
                publications.c.action == "publish")).all()
            event_count = conn.execute(select(events.c.id).where(
                events.c.article_id == article_id,
                events.c.kind == "scheduled")).all()
        self.assertEqual(len(pub_count), 1)
        self.assertEqual(len(event_count), 1)

        self.repo.edit(article_id, "clerk", 1, "Draft", "Draft body", "en",
                       self.sources, access_review=self.access)
        with self.repo.engine.connect() as conn:
            pub_state = conn.execute(select(publications.c.state).where(
                publications.c.article_id == article_id,
                publications.c.revision == 1,
                publications.c.action == "publish")).scalar_one()
            old_approval = conn.execute(select(approvals.c.sha256).where(
                approvals.c.article_id == article_id,
                approvals.c.revision == 1)).scalar_one()
        self.assertEqual(pub_state, "failed")
        self.assertEqual(old_approval, approved_sha)
        new_sha = self._sha(article_id, 2)
        with self.assertRaises(Conflict):
            self.repo.approve(article_id, 2, new_sha, "clerk", self.sources)


if __name__ == "__main__":
    unittest.main()