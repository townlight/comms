"""SQLite workflow tests for encrypted subscription commands and mail delivery.

These tests exercise the real migrations and CommunicationsRepository against a
temporary SQLite database. They do not use SMTP or any network calls.
"""

import json
import os
import re
import tempfile
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, func, select

from townlight_comms import migrations
from townlight_comms.models import subscribers
from townlight_comms.store import Conflict, InvalidInput, Unauthorized
from townlight_comms.subscription_models import (
    subscription_commands,
    subscription_mail,
)
from townlight_comms.workflow import CommunicationsRepository


class SubscriptionCommandRepositoryTests(unittest.TestCase):
    public_origin = "https://townlight.example"

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.engine = create_engine("sqlite:///" + self.path)
        migrations.upgrade(self.engine)
        self.repo = CommunicationsRepository(self.engine)
        self.engine = self.repo.engine
        self.key = Fernet.generate_key()

    def tearDown(self):
        self.engine.dispose()
        os.unlink(self.path)

    def _execute(self, command_id, operation, payload):
        return self.repo.execute_subscription_command(
            command_id,
            operation,
            payload,
            key=self.key,
            public_origin=self.public_origin,
        )

    def _count(self, table):
        with self.engine.connect() as conn:
            return conn.execute(
                select(func.count()).select_from(table)
            ).scalar_one()

    def _command_exists(self, command_id):
        with self.engine.connect() as conn:
            return conn.execute(
                select(subscription_commands.c.command_id).where(
                    subscription_commands.c.command_id == command_id
                )
            ).first() is not None

    def _subscriber(self, email):
        with self.engine.connect() as conn:
            return conn.execute(
                select(subscribers).where(subscribers.c.email == email)
            ).mappings().one()

    def _mail_for(self, recipient):
        with self.engine.connect() as conn:
            return dict(conn.execute(
                select(subscription_mail).where(
                    subscription_mail.c.recipient == recipient
                )
            ).mappings().one())

    def _mail_by_id(self, mail_id):
        with self.engine.connect() as conn:
            return dict(conn.execute(
                select(subscription_mail).where(
                    subscription_mail.c.id == mail_id
                )
            ).mappings().one())

    def _mail_payload_and_tokens(self, recipient):
        row = self._mail_for(recipient)
        payload = json.loads(
            Fernet(self.key)
            .decrypt(row["encrypted_body"].encode("utf-8"))
            .decode("utf-8")
        )
        tokens = {}
        for url in re.findall(r"https?://\S+", payload["body"]):
            parts = urlsplit(url)
            query = parse_qs(parts.query)
            if parts.path.endswith("/confirm/"):
                tokens["confirm"] = query["token"][0]
            elif parts.path.endswith("/preferences/"):
                tokens["preferences"] = query["token"][0]
        self.assertEqual(set(tokens), {"confirm", "preferences"})
        return row, payload, tokens

    def _subscribe(self, email):
        return self._execute(
            str(uuid.uuid4()),
            "comms_subscribe",
            {"email": email, "topics": ["all"], "language": "en"},
        )

    def test_subscribe_is_generic_idempotent_and_tokens_stay_encrypted(self):
        email = "person@example.com"
        command_id = str(uuid.uuid4())
        payload = {"email": email, "topics": ["all"], "language": "en"}

        result = self._execute(command_id, "comms_subscribe", payload)
        self.assertEqual(result, {"accepted": True})

        subscriber = self._subscriber(email)
        self.assertFalse(subscriber["active"])
        self.assertFalse(subscriber["confirmed"])
        self.assertIsNotNone(subscriber["confirmation_hash"])
        self.assertIsNotNone(subscriber["token_hash"])

        mail, message, tokens = self._mail_payload_and_tokens(email)
        self.assertEqual(mail["state"], "pending")
        self.assertEqual(mail["recipient"], email)
        self.assertEqual(mail["attempts"], 0)
        self.assertIsNone(mail["claim_token"])
        self.assertIsNone(mail["lease_until"])
        self.assertIsNotNone(mail["next_attempt_at"])
        self.assertTrue(mail["message_id"].endswith("@townlight.local"))
        self.assertNotIn(email, mail["encrypted_body"])
        self.assertNotIn(tokens["confirm"], mail["encrypted_body"])
        self.assertNotIn(tokens["preferences"], mail["encrypted_body"])
        self.assertEqual(message["subject"], "Confirm your subscription")
        self.assertIn("/communications/confirm/?token=", message["body"])
        self.assertIn("/communications/preferences/?token=", message["body"])

        self.assertEqual(self._count(subscription_commands), 1)
        self.assertEqual(self._count(subscription_mail), 1)

        replay = self._execute(command_id, "comms_subscribe", payload)
        self.assertEqual(replay, {"accepted": True})
        self.assertEqual(self._count(subscription_commands), 1)
        self.assertEqual(self._count(subscription_mail), 1)

        with self.assertRaises(Conflict):
            self._execute(
                command_id,
                "comms_subscribe",
                {"email": email, "topics": ["parks"], "language": "en"},
            )
        self.assertEqual(self._count(subscription_commands), 1)
        self.assertEqual(self._count(subscription_mail), 1)

        confirmed = self._execute(
            str(uuid.uuid4()),
            "comms_confirm",
            {"token": tokens["confirm"]},
        )
        self.assertEqual(confirmed, {"confirmed": True})
        subscriber = self._subscriber(email)
        self.assertTrue(subscriber["active"])
        self.assertTrue(subscriber["confirmed"])
        self.assertIsNone(subscriber["confirmation_hash"])

        preferences = self._execute(
            str(uuid.uuid4()),
            "comms_preferences",
            {"token": tokens["preferences"], "active": False},
        )
        self.assertEqual(set(preferences), {"topics", "language", "active"})
        self.assertFalse(preferences["active"])
        dumped = json.dumps(preferences, sort_keys=True)
        self.assertNotIn(tokens["confirm"], dumped)
        self.assertNotIn(tokens["preferences"], dumped)
        self.assertNotIn("token", dumped)
        self.assertFalse(self._subscriber(email)["active"])

    def test_invalid_commands_leave_no_receipt_and_do_not_reset_confirmation(self):
        email = "person@example.com"
        self._subscribe(email)
        _, _, tokens = self._mail_payload_and_tokens(email)
        original = self._subscriber(email)

        command_count = self._count(subscription_commands)
        subscriber_count = self._count(subscribers)
        mail_count = self._count(subscription_mail)

        cases = [
            (
                str(uuid.uuid4()),
                "comms_confirm",
                {"token": "x" * 32},
                Unauthorized,
            ),
            (
                str(uuid.uuid4()),
                "comms_subscribe",
                {
                    "email": "second@example.com",
                    "topics": ["all"],
                    "extra": True,
                },
                InvalidInput,
            ),
            (
                str(uuid.uuid4()),
                "comms_preferences",
                {"token": tokens["preferences"], "active": 1},
                Unauthorized,
            ),
            (
                str(uuid.uuid4()),
                "comms_subscribe",
                {"email": "not-an-email", "topics": ["all"]},
                InvalidInput,
            ),
        ]
        for command_id, operation, payload, exception in cases:
            with self.subTest(operation=operation, payload=payload):
                with self.assertRaises(exception):
                    self._execute(command_id, operation, payload)
                self.assertFalse(self._command_exists(command_id))
                self.assertEqual(self._count(subscription_commands), command_count)
                self.assertEqual(self._count(subscribers), subscriber_count)
                self.assertEqual(self._count(subscription_mail), mail_count)

        same_email_command = str(uuid.uuid4())
        repeated = self._execute(
            same_email_command,
            "comms_subscribe",
            {"email": email, "topics": ["parks"], "language": "fr"},
        )
        self.assertEqual(repeated, {"accepted": True})
        self.assertEqual(self._count(subscription_commands), command_count + 1)
        self.assertEqual(self._count(subscribers), subscriber_count)
        self.assertEqual(self._count(subscription_mail), mail_count)

        after = self._subscriber(email)
        self.assertEqual(
            after["confirmation_hash"], original["confirmation_hash"])
        self.assertEqual(after["token_hash"], original["token_hash"])
        self.assertFalse(after["active"])
        self.assertFalse(after["confirmed"])

        self.assertEqual(
            self._execute(
                str(uuid.uuid4()),
                "comms_confirm",
                {"token": tokens["confirm"]},
            ),
            {"confirmed": True},
        )

        invalid_preferences_command = str(uuid.uuid4())
        with self.assertRaises(InvalidInput):
            self._execute(
                invalid_preferences_command,
                "comms_preferences",
                {"token": tokens["preferences"], "active": 1},
            )
        self.assertFalse(self._command_exists(invalid_preferences_command))

    def test_claim_sent_expiry_and_bounded_explicit_retry(self):
        first_email = "first@example.com"
        self._subscribe(first_email)
        first_mail = self._mail_for(first_email)

        first_now = datetime.now(UTC)
        claimed = self.repo.claim_subscription_email(
            key=self.key, now=first_now, lease_seconds=30)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["id"], first_mail["id"])
        self.assertEqual(claimed["recipient"], first_email)
        self.assertEqual(claimed["subject"], "Confirm your subscription")
        self.assertIn("/communications/confirm/?token=", claimed["body"])

        self.assertFalse(self.repo.finish_subscription_email(
            claimed["id"], "wrong-token", "sent", now=first_now))
        still_claimed = self._mail_by_id(claimed["id"])
        self.assertEqual(still_claimed["state"], "claimed")
        self.assertEqual(still_claimed["claim_token"], claimed["claim_token"])

        self.assertTrue(self.repo.finish_subscription_email(
            claimed["id"], claimed["claim_token"], "sent", now=first_now))
        sent = self._mail_by_id(claimed["id"])
        self.assertEqual(sent["state"], "sent")
        self.assertEqual(sent["attempts"], 1)
        self.assertIsNone(sent["claim_token"])
        self.assertIsNone(sent["lease_until"])
        self.assertIsNone(sent["last_error"])
        self.assertIsNone(self.repo.claim_subscription_email(
            key=self.key, now=first_now + timedelta(seconds=1)))

        second_email = "second@example.com"
        self._subscribe(second_email)
        second_mail = self._mail_for(second_email)
        second_now = datetime.now(UTC)
        second = self.repo.claim_subscription_email(
            key=self.key, now=second_now, lease_seconds=30)
        self.assertIsNotNone(second)
        self.assertEqual(second["id"], second_mail["id"])

        expired_now = second_now + timedelta(seconds=31)
        self.assertIsNone(self.repo.claim_subscription_email(
            key=self.key, now=expired_now))
        expired = self._mail_by_id(second["id"])
        self.assertEqual(expired["state"], "uncertain")
        self.assertEqual(expired["last_error"], "lease expired")
        self.assertEqual(expired["attempts"], 1)
        self.assertIsNone(expired["claim_token"])
        self.assertIsNone(expired["lease_until"])
        self.assertFalse(self.repo.finish_subscription_email(
            second["id"], second["claim_token"], "retry", now=expired_now))

        retry_email = "retry@example.com"
        self._subscribe(retry_email)
        retry_mail = self._mail_for(retry_email)
        due = datetime.now(UTC)
        claimed = self.repo.claim_subscription_email(
            key=self.key, now=due, lease_seconds=30)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["id"], retry_mail["id"])

        for attempt in range(1, 6):
            row = self._mail_by_id(claimed["id"])
            self.assertEqual(row["state"], "claimed")
            self.assertEqual(row["attempts"], attempt)
            self.assertTrue(self.repo.finish_subscription_email(
                claimed["id"], claimed["claim_token"], "retry", now=due))
            row = self._mail_by_id(claimed["id"])
            if attempt < 5:
                self.assertEqual(row["state"], "pending")
                self.assertEqual(row["last_error"], "delivery retry scheduled")
                self.assertEqual(row["attempts"], attempt)
                self.assertIsNotNone(row["next_attempt_at"])
                self.assertIsNone(row["claim_token"])
                self.assertIsNone(row["lease_until"])
                self.assertIsNone(self.repo.claim_subscription_email(
                    key=self.key, now=due))
                due = row["next_attempt_at"]
                claimed = self.repo.claim_subscription_email(
                    key=self.key, now=due, lease_seconds=30)
                self.assertIsNotNone(claimed)
                self.assertEqual(claimed["id"], retry_mail["id"])
            else:
                self.assertEqual(row["state"], "failed")
                self.assertEqual(row["last_error"], "delivery failed")
                self.assertEqual(row["attempts"], 5)
                self.assertIsNone(row["claim_token"])
                self.assertIsNone(row["lease_until"])
                self.assertIsNone(self.repo.claim_subscription_email(
                    key=self.key, now=due + timedelta(days=1)))


if __name__ == "__main__":
    unittest.main()
