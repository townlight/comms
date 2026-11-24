"""Workflow-level tests for publication email outbox delivery.

These tests exercise the real CommunicationsRepository against an in-memory
SQLite database migrated through townlight_comms.migrations. No SMTP or other
network calls are made.
"""

import hashlib
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select

from townlight_comms import migrations
from townlight_comms.models import email_outbox, revisions
from townlight_comms.workflow import CommunicationsRepository


class EmailOutboxWorkflowTests(unittest.TestCase):
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

    def _subscribe(self, email, topics, language="en"):
        requested = self.repo.request_subscription(email, topics, language)
        self.assertIn("management_token", requested)
        self.assertIn("confirmation_token", requested)
        self.assertEqual(
            self.repo.confirm_subscription(requested["confirmation_token"]),
            {"confirmed": True})
        return requested

    def _claimed_publication(self):
        article_id = self.repo.create(
            "clerk", "announcement", "Notice", "Body", "en", self.sources,
            access_review=self.access)["id"]
        self.repo.review(article_id, "clerk", 1, "checked")
        sha = self._sha(article_id, 1)
        self.repo.approve(article_id, 1, sha, "clerk", self.sources)
        due = datetime.now(UTC) - timedelta(seconds=1)
        self.repo.schedule(
            article_id, 1, sha, "clerk", due, ["email", "website"])
        job = self.repo.claim_due()
        self.assertIsNotNone(job)
        self.assertIn("id", job)
        self.assertIn("claim_token", job)
        return job

    @staticmethod
    def _public_url():
        return "https://townlight.example/public/notice"

    def test_enqueue_eligibility_is_idempotent_and_claim_delivers(self):
        eligible = self._subscribe("eligible@example.com", ["all"], "en")
        self._subscribe("parks@example.com", ["parks"], "en")
        self._subscribe("french@example.com", ["all"], "fr")

        job = self._claimed_publication()
        url = self._public_url()

        counts = self.repo.enqueue_publication_email(
            job["id"], job["claim_token"], url)
        self.assertEqual(counts, {
            "pending": 1, "sent": 0, "failed": 0,
            "uncertain": 0, "skipped": 0,
        })

        duplicate = self.repo.enqueue_publication_email(
            job["id"], job["claim_token"], url)
        self.assertEqual(duplicate, {
            "pending": 1, "sent": 0, "failed": 0,
            "uncertain": 0, "skipped": 0,
        })

        claimed = self.repo.claim_email(now=datetime.now(UTC))
        self.assertIsNotNone(claimed)
        expected_message_id = "<{}@townlight.local>".format(
            hashlib.sha256(
                "{}:{}".format(job["id"], eligible["id"]).encode("utf-8")
            ).hexdigest()
        )
        self.assertEqual(claimed["message_id"], expected_message_id)
        self.assertEqual(claimed["subject"], "Notice")
        self.assertEqual(claimed["recipient"], "eligible@example.com")

        with self.repo.engine.connect() as conn:
            row = conn.execute(select(
                email_outbox.c.subject,
                email_outbox.c.body,
                email_outbox.c.message_id,
            ).where(email_outbox.c.id == claimed["id"])).mappings().one()
        self.assertEqual(row["subject"], "Notice")
        self.assertEqual(row["message_id"], expected_message_id)
        self.assertTrue(row["body"].endswith(url))

        self.assertTrue(self.repo.finish_email(
            claimed["id"], claimed["claim_token"], "sent"))
        summary = self.repo.email_summary(job["id"])
        self.assertEqual(summary["sent"], 1)
        self.assertEqual(summary["remaining"], 0)

    def test_unsubscribe_after_enqueue_skips_delivery(self):
        subscriber = self._subscribe("unsubscribe@example.com", ["all"], "en")
        job = self._claimed_publication()
        counts = self.repo.enqueue_publication_email(
            job["id"], job["claim_token"], self._public_url())
        self.assertEqual(counts["pending"], 1)

        preferences = self.repo.preferences(
            subscriber["management_token"], active=False)
        self.assertFalse(preferences["active"])

        self.assertIsNone(self.repo.claim_email(
            now=datetime.now(UTC)))
        summary = self.repo.email_summary(job["id"])
        self.assertEqual(summary["remaining"], 0)
        self.assertEqual(summary["sent"], 0)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["failed"], 0)

        with self.repo.engine.connect() as conn:
            row = conn.execute(select(
                email_outbox.c.state,
                email_outbox.c.attempts,
                email_outbox.c.claim_token,
            ).where(email_outbox.c.publication_id == job["id"])).mappings().one()
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["attempts"], 0)
        self.assertIsNone(row["claim_token"])

    def test_expired_claim_is_uncertain_and_retry_keeps_pending(self):
        expire_subscriber = self._subscribe("expire@example.com", ["all"], "en")
        job = self._claimed_publication()
        self.repo.enqueue_publication_email(
            job["id"], job["claim_token"], self._public_url())

        first_now = datetime.now(UTC)
        claimed = self.repo.claim_email(now=first_now, lease_seconds=30)
        self.assertIsNotNone(claimed)

        expired_now = first_now + timedelta(seconds=31)
        self.assertIsNone(self.repo.claim_email(now=expired_now))
        self.assertIsNone(self.repo.claim_email(now=expired_now))

        with self.repo.engine.connect() as conn:
            expired = conn.execute(select(
                email_outbox.c.state,
                email_outbox.c.attempts,
                email_outbox.c.claim_token,
            ).where(email_outbox.c.id == claimed["id"])).mappings().one()
        self.assertEqual(expired["state"], "uncertain")
        self.assertEqual(expired["attempts"], 1)
        self.assertIsNone(expired["claim_token"])

        # Deactivate the expired subscriber so only the retry recipient is
        # eligible for the separate retry publication.
        preferences = self.repo.preferences(
            expire_subscriber["management_token"], active=False)
        self.assertFalse(preferences["active"])

        # A separate publication/subscriber fixture exercises safe retry.
        self._subscribe("retry@example.com", ["all"], "en")
        retry_job = self._claimed_publication()
        retry_counts = self.repo.enqueue_publication_email(
            retry_job["id"], retry_job["claim_token"], self._public_url())
        self.assertEqual(retry_counts["pending"], 1)

        retry_now = datetime.now(UTC)
        retry_claim = self.repo.claim_email(now=retry_now)
        self.assertIsNotNone(retry_claim)
        self.assertFalse(self.repo.finish_email(
            retry_claim["id"], "wrong-token", "retry"))
        self.assertTrue(self.repo.finish_email(
            retry_claim["id"], retry_claim["claim_token"], "retry"))

        with self.repo.engine.connect() as conn:
            retried = conn.execute(select(
                email_outbox.c.state,
                email_outbox.c.attempts,
                email_outbox.c.next_attempt_at,
                email_outbox.c.claim_token,
            ).where(email_outbox.c.id == retry_claim["id"])).mappings().one()
        self.assertEqual(retried["state"], "pending")
        self.assertEqual(retried["attempts"], 1)
        self.assertIsNotNone(retried["next_attempt_at"])
        self.assertIsNone(retried["claim_token"])

        # next_attempt_at is in the future, so the pending row is not
        # re-claimed immediately.
        self.assertIsNone(self.repo.claim_email(now=retry_now))
        summary = self.repo.email_summary(retry_job["id"])
        self.assertEqual(summary["remaining"], 1)
        self.assertEqual(summary["sent"], 0)


if __name__ == "__main__":
    unittest.main()
