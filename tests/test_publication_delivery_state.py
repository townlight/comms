"""Real tests for publication claim delivery and subscription state."""

import hashlib
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select

from townlight_comms import migrations
from townlight_comms.models import publications, revisions, subscribers
from townlight_comms.store import Conflict, InvalidInput, Unauthorized
from townlight_comms.workflow import CommunicationsRepository


class PublicationDeliveryStateTests(unittest.TestCase):
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

    def test_claim_and_finish_guard_delivery_state(self):
        article_id = self._approved_due()
        job = self.repo.claim_due()
        self.assertIsNotNone(job)
        self.assertIsNone(CommunicationsRepository(self.engine).claim_due())
        with self.assertRaises(Conflict):
            self.repo.finish_publication(job["id"], "wrong-token", state="published")
        with self.assertRaises(InvalidInput):
            self.repo.finish_publication(job["id"], job["claim_token"], state="published")
        self.repo.finish_publication(job["id"], job["claim_token"], state="queued")
        self.assertEqual(self.repo.get(article_id)["state"], "scheduled")

    def test_expired_claim_becomes_uncertain_without_resend(self):
        self._approved_due()
        job = self.repo.claim_due()
        expired_now = job["lease_until"] + timedelta(seconds=1)
        self.assertIsNone(self.repo.claim_due(now=expired_now))
        with self.repo.engine.connect() as conn:
            state = conn.execute(select(publications.c.state).where(
                publications.c.id == job["id"])).scalar_one()
        self.assertEqual(state, "uncertain")
        self.assertIsNone(self.repo.claim_due(now=expired_now))

    def test_subscription_confirmation_preferences_and_hashed_tokens(self):
        requested = self.repo.request_subscription("user@example.com", ["meetings", "code"])
        management = requested["management_token"]
        confirmation = requested["confirmation_token"]
        with self.assertRaises(Unauthorized):
            self.repo.preferences(management)

        duplicate = self.repo.request_subscription("USER@Example.com", ["parks"])
        self.assertEqual(duplicate, {"accepted": True})

        with self.repo.engine.connect() as conn:
            row = conn.execute(select(subscribers).where(
                subscribers.c.id == requested["id"])).mappings().one()
        self.assertEqual(row["token_hash"],
                         hashlib.sha256(management.encode("utf-8")).hexdigest())
        self.assertEqual(row["confirmation_hash"],
                         hashlib.sha256(confirmation.encode("utf-8")).hexdigest())
        self.assertNotIn(management, row.values())
        self.assertNotIn(confirmation, row.values())

        self.assertEqual(self.repo.confirm_subscription(confirmation), {"confirmed": True})
        response = self.repo.preferences(management, active=False)
        self.assertEqual(set(response), {"id", "topics", "language", "active"})
        self.assertEqual(response["id"], requested["id"])
        self.assertEqual(response["topics"], ["meetings", "code"])
        self.assertEqual(response["language"], "en")
        self.assertFalse(response["active"])


if __name__ == "__main__":
    unittest.main()
