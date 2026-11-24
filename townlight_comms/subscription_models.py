"""Durable subscription command and encrypted-mail tables.

Uses the shared ``townlight_comms`` metadata/SCHEMA from :mod:`townlight_comms.models`.
Mail bodies are stored only as Fernet ciphertext containing subject, body and tokens.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    text,
)

from .models import SCHEMA, metadata, subscribers

MAIL_STATES = ("pending", "claimed", "sent", "failed", "uncertain")
_STATE_CHECK = "state IN ('pending', 'claimed', 'sent', 'failed', 'uncertain')"

subscription_commands = Table(
    "subscription_commands",
    metadata,
    Column("command_id", String(36), primary_key=True),
    Column("operation", String(32), nullable=False),
    Column("payload_sha256", String(64), nullable=False),
    Column("result", JSON, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_subscription_commands_created", "created_at"),
)

subscription_mail = Table(
    "subscription_mail",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "subscriber_id",
        String(36),
        ForeignKey(subscribers.c.id, ondelete="CASCADE", name="fk_subscription_mail_subscriber"),
        nullable=False,
    ),
    # Fernet ciphertext only: subject, body and all tokens remain encrypted.
    Column("encrypted_body", Text, nullable=False),
    Column("recipient", String(320), nullable=False),
    Column("message_id", String(255), nullable=False, unique=True),
    Column("state", String(16), nullable=False),
    Column("claim_token", String(36), nullable=True),
    Column("lease_until", DateTime(timezone=True), nullable=True),
    Column("attempts", Integer, nullable=False, default=0, server_default=text("0")),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("last_error", String(64), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(_STATE_CHECK, name="ck_subscription_mail_state"),
    CheckConstraint("attempts >= 0", name="ck_subscription_mail_attempts_nonnegative"),
    Index("ix_subscription_mail_due", "state", "next_attempt_at"),
    Index("ix_subscription_mail_lease", "state", "lease_until"),
    Index("ix_subscription_mail_subscriber", "subscriber_id"),
)

subscription_preferences_keys = Table(
    'subscription_preferences_keys',
    metadata,
    Column(
        'subscriber_id',
        String(36),
        ForeignKey(
            subscribers.c.id,
            ondelete='CASCADE',
            name='fk_subscription_preferences_keys_subscriber',
        ),
        primary_key=True,
    ),
    Column('encrypted_token', Text, nullable=False),
)

__all__ = [
    "MAIL_STATES",
    "SCHEMA",
    "metadata",
    "subscription_commands",
    "subscription_mail",
]
