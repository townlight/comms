"""Real PostgreSQL integration test for the editorial delivery workflow."""

import os
import unittest
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url

from townlight_comms import migrations
from townlight_comms.models import publications, revisions
from townlight_comms.workflow import CommunicationsRepository


class PostgresEditorialIntegrationTests(unittest.TestCase):
    def test_editorial_claim_finish_and_reopen(self):
        raw_url = os.environ.get("TOWNLIGHT_COMMS_TEST_DATABASE_URL")
        if not raw_url:
            self.skipTest("TOWNLIGHT_COMMS_TEST_DATABASE_URL is not set")
        url = make_url(raw_url)
        if url.get_backend_name() != "postgresql":
            self.fail("test database URL must use PostgreSQL")
        if url.host not in {"127.0.0.1", "localhost"}:
            self.fail("test database host must be 127.0.0.1 or localhost")
        if url.port != 25483:
            self.fail("test database port must be 25483")
        if not (url.database or "").startswith("townlight_comms_test"):
            self.fail("test database name must start with townlight_comms_test")

        engine = create_engine(raw_url)
        try:
            migrations.upgrade(engine)
            migrations.upgrade(engine)
            self.assertTrue(migrations.ready(engine))

            repo = CommunicationsRepository(engine)
            unique = uuid4().hex
            sources = [{
                "kind": "meetings", "id": "meeting-" + unique, "revision": "1",
                "title": "Integration meeting", "url": "/public/meetings/" + unique,
                "sha256": "b" * 64, "eligible": True,
            }]
            access = {"reviewed": True, "findings": []}
            article_id = repo.create(
                "clerk", "announcement", "Postgres notice " + unique,
                "Body", "en", sources, access_review=access)["id"]
            repo.review(article_id, "clerk", 1, "checked")
            with engine.connect() as conn:
                sha = conn.execute(select(revisions.c.sha256).where(
                    revisions.c.article_id == article_id,
                    revisions.c.revision == 1)).scalar_one()
            repo.approve(article_id, 1, sha, "clerk", sources)
            repo.schedule(
                article_id, 1, sha, "clerk", datetime.now(UTC),
                ["website"])

            job = repo.claim_due()
            self.assertIsNotNone(job)
            self.assertEqual(job["article_id"], article_id)
            receipt = {"status": "queued", "job": job["id"]}
            repo.finish_publication(
                job["id"], job["claim_token"], state="queued",
                website_receipt=receipt)

            engine.dispose()
            engine = create_engine(raw_url)
            reopened = CommunicationsRepository(engine)
            record = reopened.get(article_id)
            self.assertEqual(record["id"], article_id)
            with engine.connect() as conn:
                current_revision = conn.execute(
                    select(revisions.c.revision).where(
                        revisions.c.article_id == article_id
                    ).order_by(revisions.c.revision.desc())
                ).scalars().first()
                history_state = conn.execute(
                    select(publications.c.state).where(
                        publications.c.id == job["id"])
                ).scalar_one()
            self.assertEqual(current_revision, 1)
            self.assertEqual(history_state, "queued")
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
