"""Durable Comms contract — SQLAlchemy Core tables (schema townlight_comms).

Revisions/approvals are append-only; enforcement lives in the runtime layer.
All timestamps are timezone-aware DateTime values normalized to UTC by the caller.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)

SCHEMA = "townlight_comms"
metadata = MetaData(schema=SCHEMA)

ARTICLE_STATES = ("draft", "review", "approved", "scheduled", "published", "withdrawn")
ARTICLE_KINDS = ("meeting_summary", "ordinance_explainer", "announcement", "faq", "newsletter")
SOURCE_KINDS = ("meetings", "code", "notice", "311", "parks", "library", "elections")

PUBLICATION_ACTIONS = ("publish", "correct", "withdraw")
PUBLICATION_STATES = ("pending", "claimed", "queued", "published", "failed", "uncertain")
EMAIL_STATES = ("pending", "claimed", "queued", "sent", "failed", "uncertain")

UUID = String(36)


def _enum(col: str, values: tuple) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{col} IN ({joined})"


articles = Table(
    "articles",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("title", String(512), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("current_revision", Integer, nullable=False),
    Column("state", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("current_revision > 0", name="ck_articles_current_revision_positive"),
    CheckConstraint(_enum("state", ARTICLE_STATES), name="ck_articles_state"),
    CheckConstraint(_enum("kind", ARTICLE_KINDS), name="ck_articles_kind"),
    Index("ix_articles_state_updated", "state", "updated_at"),
)

revisions = Table(
    "revisions",
    metadata,
    Column(
        "article_id",
        UUID,
        ForeignKey(f"{SCHEMA}.articles.id", ondelete="CASCADE", name="fk_revisions_article"),
        primary_key=True,
    ),
    Column("revision", Integer, primary_key=True),
    Column("title", String(512), nullable=False),
    Column("body", Text, nullable=False),
    Column("language", String(16), nullable=False),
    Column("source_refs", JSON, nullable=False),
    Column("attachment_refs", JSON, nullable=False, default=list),
    Column("access_review", JSON, nullable=False),
    Column("translation_provenance", JSON, nullable=True),
    Column("sha256", String(64), nullable=False),
    Column("actor", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("revision > 0", name="ck_revisions_revision_positive"),
    Index("ix_revisions_article", "article_id", "revision"),
)

attachments = Table(
    "attachments",
    metadata,
    Column("id", UUID, primary_key=True),
    Column(
        "article_id",
        UUID,
        ForeignKey(f"{SCHEMA}.articles.id", ondelete="CASCADE", name="fk_attachments_article"),
        nullable=False,
    ),
    Column("revision", Integer, nullable=False),
    Column("name", String(512), nullable=False),
    Column("media_type", String(128), nullable=False),
    Column("content", LargeBinary, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("size", Integer, nullable=False),
    Column("public", Boolean, nullable=False, server_default=text("false")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("revision > 0", name="ck_attachments_revision_positive"),
    CheckConstraint("size >= 0", name="ck_attachments_size_nonnegative"),
    Index("ix_attachments_article_revision", "article_id", "revision"),
)

approvals = Table(
    "approvals",
    metadata,
    Column(
        "article_id",
        UUID,
        ForeignKey(f"{SCHEMA}.articles.id", ondelete="CASCADE", name="fk_approvals_article"),
        primary_key=True,
    ),
    Column("revision", Integer, primary_key=True),
    Column("sha256", String(64), nullable=False),
    Column("actor", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("revision > 0", name="ck_approvals_revision_positive"),
)

approved_source_snapshots = Table(
    "approved_source_snapshots",
    metadata,
    Column(
        "article_id",
        UUID,
        ForeignKey(f"{SCHEMA}.articles.id", ondelete="CASCADE", name="fk_approved_source_snapshots_article"),
        primary_key=True,
    ),
    Column("revision", Integer, primary_key=True),
    Column("revision_sha256", String(64), nullable=False),
    Column("snapshots", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("revision > 0", name="ck_approved_source_snapshots_revision_positive"),
)

publications = Table(
    "publications",
    metadata,
    Column("id", UUID, primary_key=True),
    Column(
        "article_id",
        UUID,
        ForeignKey(f"{SCHEMA}.articles.id", ondelete="CASCADE", name="fk_publications_article"),
        nullable=False,
    ),
    Column("revision", Integer, nullable=False),
    Column("action", String(16), nullable=False),
    Column("channels", JSON, nullable=False),
    Column("due_at", DateTime(timezone=True), nullable=False),
    Column("state", String(16), nullable=False),
    Column("claim_token", String(64), nullable=True),
    Column("lease_until", DateTime(timezone=True), nullable=True),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("next_attempt_at", DateTime(timezone=True), nullable=True),
    Column("website_receipt", JSON, nullable=True),
    Column("email_summary", JSON, nullable=True),
    Column("last_error", Text, nullable=True),
    Column("actor", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("revision > 0", name="ck_publications_revision_positive"),
    CheckConstraint("attempts >= 0", name="ck_publications_attempts_nonnegative"),
    CheckConstraint(_enum("action", PUBLICATION_ACTIONS), name="ck_publications_action"),
    CheckConstraint(_enum("state", PUBLICATION_STATES), name="ck_publications_state"),
    UniqueConstraint(
        "article_id", "revision", "action",
        name="uq_publications_article_revision_action",
    ),
    Index("ix_publications_due", "state", "due_at"),
    Index("ix_publications_lease", "state", "lease_until"),
)

subscribers = Table(
    "subscribers",
    metadata,
    Column("id", UUID, primary_key=True),
    Column("email", String(320), nullable=False),
    Column("address_hash", String(64), nullable=False, unique=True),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("topics", JSON, nullable=False),
    Column("language", String(16), nullable=False),
    Column("active", Boolean, nullable=False, server_default=text("true")),
    Column("confirmed", Boolean, nullable=False, server_default=text("false")),
    Column("confirmation_hash", String(64), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Index("ix_subscribers_active", "active"),
)

email_outbox = Table(
    "email_outbox",
    metadata,
    Column("id", UUID, primary_key=True),
    Column(
        "publication_id",
        UUID,
        ForeignKey(f"{SCHEMA}.publications.id", ondelete="CASCADE", name="fk_email_outbox_publication"),
        nullable=False,
    ),
    Column(
        "subscriber_id",
        UUID,
        ForeignKey(f"{SCHEMA}.subscribers.id", ondelete="CASCADE", name="fk_email_outbox_subscriber"),
        nullable=False,
    ),
    Column("message_id", String(255), nullable=False, unique=True),
    Column("recipient", String(320), nullable=False),
    Column("subject", String(512), nullable=False),
    Column("body", Text, nullable=False),
    Column("state", String(16), nullable=False),
    Column("attempts", Integer, nullable=False, server_default=text("0")),
    Column("next_attempt_at", DateTime(timezone=True), nullable=True),
    Column("claim_token", String(64), nullable=True),
    Column("lease_until", DateTime(timezone=True), nullable=True),
    Column("last_error", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("sent_at", DateTime(timezone=True), nullable=True),
    CheckConstraint("attempts >= 0", name="ck_email_outbox_attempts_nonnegative"),
    CheckConstraint(_enum("state", EMAIL_STATES), name="ck_email_outbox_state"),
    UniqueConstraint(
        "publication_id", "subscriber_id",
        name="uq_email_outbox_publication_subscriber",
    ),
    Index("ix_email_outbox_due", "state", "next_attempt_at"),
    Index("ix_email_outbox_lease", "state", "lease_until"),
)

events = Table(
    "events",
    metadata,
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True),
    Column(
        "article_id",
        UUID,
        ForeignKey(f"{SCHEMA}.articles.id", ondelete="CASCADE", name="fk_events_article"),
        nullable=False,
    ),
    Column("revision", Integer, nullable=True),
    Column("kind", String(32), nullable=False),
    Column("actor", String(128), nullable=False),
    Column("payload", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("revision IS NULL OR revision > 0", name="ck_events_revision_positive"),
    Index("ix_events_article_created", "article_id", "created_at"),
    Index("ix_events_kind_created", "kind", "created_at"),
)

records_receipts = Table(
    "records_receipts",
    metadata,
    Column(
        "article_id",
        UUID,
        ForeignKey(f"{SCHEMA}.articles.id", ondelete="CASCADE", name="fk_records_receipts_article"),
        primary_key=True,
    ),
    Column("revision", Integer, primary_key=True),
    Column("archive_sha256", String(64), nullable=False),
    Column("records_id", String(128), nullable=False),
    Column("file_id", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("revision > 0", name="ck_records_receipts_revision_positive"),
    Index("ix_records_receipts_records_id", "records_id"),
)

__all__ = [
    "ARTICLE_KINDS",
    "ARTICLE_STATES",
    "EMAIL_STATES",
    "PUBLICATION_ACTIONS",
    "PUBLICATION_STATES",
    "SCHEMA",
    "SOURCE_KINDS",
    "approvals",
    "approved_source_snapshots",
    "articles",
    "attachments",
    "email_outbox",
    "events",
    "metadata",
    "publications",
    "records_receipts",
    "revisions",
    "subscribers",
]
