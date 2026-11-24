from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from sqlalchemy import func, select

from .models import approvals, articles, email_outbox, publications, revisions, subscribers
from .store import Conflict, InvalidInput, NotFound, Unauthorized, new_id, utcnow

_CRLF = re.compile(r"[\r\n]+")


class EmailEnqueueMixin:
    @staticmethod
    def _public_url(value):
        if value is None:
            return None
        if not isinstance(value, str) or not value or len(value) > 2048:
            raise InvalidInput("public_url")
        if any(ord(ch) < 32 or ord(ch) == 127 or ch.isspace() for ch in value):
            raise InvalidInput("public_url")
        if "?" in value or "#" in value:
            raise InvalidInput("public_url")
        parts = urlsplit(value)
        if parts.scheme.lower() != "https" or not parts.hostname:
            raise InvalidInput("public_url")
        if parts.username is not None or parts.password is not None:
            raise InvalidInput("public_url")
        if parts.query or parts.fragment:
            raise InvalidInput("public_url")
        return value

    @staticmethod
    def _topics(value):
        if isinstance(value, str):
            return {value}
        if not isinstance(value, list):
            return set()
        return {item for item in value if isinstance(item, str)}

    @staticmethod
    def _action(pub):
        action = pub.get("action")
        if hasattr(action, "value"):
            action = action.value
        if not isinstance(action, str):
            return "publish"
        return action.strip().lower()

    def _preferences_body(self, conn, body, subscriber_id, token_hash):
        from .subscription_apply import _origin
        from .subscription_models import subscription_preferences_keys
        from .subscriptions import _hash

        row = conn.execute(
            select(subscription_preferences_keys.c.encrypted_token).where(
                subscription_preferences_keys.c.subscriber_id == subscriber_id
            )
        ).first()
        if row is None:
            return body
        token = Fernet(self.recovery_key).decrypt(
            row.encrypted_token.encode("ascii")
        ).decode("ascii")
        if _hash(token) != token_hash:
            raise Unauthorized("invalid preferences token")
        origin = _origin(self.public_origin)
        linked = f"{body}\nManage preferences or unsubscribe: {origin}/communications/preferences/?token={token}"
        return "fernet:" + Fernet(self.recovery_key).encrypt(
            linked.encode("utf-8")
        ).decode("ascii")

    def enqueue_publication_email(self, publication_id, claim_token, public_url):
        public_url = self._public_url(public_url)
        if not isinstance(claim_token, str) or not claim_token:
            raise InvalidInput("claim_token")
        now = utcnow()
        with self.engine.begin() as conn:
            pub_row = conn.execute(
                select(publications)
                .where(publications.c.id == publication_id)
                .with_for_update()
            ).mappings().first()
            if pub_row is None:
                raise NotFound(publication_id)
            pub = dict(pub_row)
            if pub["state"] != "claimed":
                raise Conflict("publication not claimed")
            if pub["claim_token"] != claim_token:
                raise Unauthorized("claim token")

            channels = pub["channels"]
            if not isinstance(channels, list) or "email" not in channels:
                raise InvalidInput("email channel")
            email_only = bool(channels) and all(channel == "email" for channel in channels)
            if public_url is None and not email_only:
                raise InvalidInput("public_url")

            article_row = conn.execute(
                select(articles).where(articles.c.id == pub["article_id"])
            ).mappings().first()
            if article_row is None:
                raise NotFound(pub["article_id"])
            article = dict(article_row)

            revision_row = conn.execute(
                select(revisions).where(
                    revisions.c.article_id == pub["article_id"],
                    revisions.c.revision == pub["revision"],
                )
            ).mappings().first()
            if revision_row is None:
                raise NotFound("revision")
            revision = dict(revision_row)

            approval_row = conn.execute(
                select(approvals).where(
                    approvals.c.article_id == pub["article_id"],
                    approvals.c.revision == pub["revision"],
                )
            ).mappings().first()
            if approval_row is None:
                raise Conflict("approval missing")
            if dict(approval_row)["sha256"] != revision["sha256"]:
                raise Conflict("approval hash mismatch")

            source_kinds = {
                ref.get("kind")
                for ref in revision.get("source_refs", [])
                if isinstance(ref, dict)
            }
            announcement = article["kind"] == "announcement"
            subscriber_rows = conn.execute(
                select(subscribers)
                .where(
                    subscribers.c.active.is_(True),
                    subscribers.c.confirmed.is_(True),
                    subscribers.c.language == revision["language"],
                )
                .order_by(subscribers.c.id)
            ).mappings().all()

            existing = {
                row["subscriber_id"]
                for row in conn.execute(
                    select(email_outbox.c.subscriber_id).where(
                        email_outbox.c.publication_id == publication_id
                    )
                ).mappings()
            }

            title = _CRLF.sub(" ", revision["title"]).strip() or "Notification"
            action = self._action(pub)
            if action in {"withdraw", "withdrawal", "withdrawn"}:
                subject = f"Withdrawn: {title}"
                body = f"This communication has been withdrawn: {title}."
            elif action in {"correct", "correction", "corrected"}:
                subject = f"Correction: {title}"
                body = revision["body"]
            else:
                subject = title
                body = revision["body"]
            if public_url is not None:
                body = f"{body}\n\n{public_url}"

            for sub_row in subscriber_rows:
                sub = dict(sub_row)
                topics = self._topics(sub["topics"])
                if announcement and not source_kinds:
                    eligible = "all" in topics
                else:
                    eligible = "all" in topics or bool(topics & source_kinds)
                if not eligible:
                    continue
                subscriber_id = sub["id"]
                if subscriber_id in existing:
                    # Already queued for this publication: idempotent no-op,
                    # not a skipped delivery. Only terminal states count.
                    continue
                message_id = "<{}@townlight.local>".format(
                    hashlib.sha256(
                        f"{publication_id}:{subscriber_id}".encode()
                    ).hexdigest()
                )
                outbox_body = self._preferences_body(
                    conn, body, subscriber_id, sub["token_hash"]
                )
                conn.execute(
                    email_outbox.insert().values(
                        id=new_id(),
                        publication_id=publication_id,
                        subscriber_id=subscriber_id,
                        message_id=message_id,
                        recipient=sub["email"],
                        subject=subject,
                        body=outbox_body,
                        state="pending",
                        attempts=0,
                        next_attempt_at=now,
                        created_at=now,
                    )
                )
                existing.add(subscriber_id)

            counts = {
                "pending": 0,
                "sent": 0,
                "failed": 0,
                "uncertain": 0,
                "skipped": 0,
            }
            for state, count in conn.execute(
                select(email_outbox.c.state, func.count())
                .where(email_outbox.c.publication_id == publication_id)
                .group_by(email_outbox.c.state)
            ):
                if state in counts:
                    counts[state] = int(count)
            return counts
