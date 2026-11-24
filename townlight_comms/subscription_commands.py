"""Idempotent subscription command execution with receipts."""

from __future__ import annotations

import copy
import hashlib
import json
from uuid import UUID

from sqlalchemy import select

from .store import Conflict, InvalidInput, utcnow
from .subscription_apply import apply_subscription
from .subscription_models import subscription_commands

_OPERATIONS = {"comms_subscribe", "comms_confirm", "comms_preferences"}
_MAX_PAYLOAD = 16 * 1024


class SubscriptionCommandMixin:
    """Mixin exposing ``execute_subscription_command``."""

    def execute_subscription_command(
        self, command_id, operation, payload, *, key, public_origin
    ):
        # Canonical UUID: require the canonical lowercase hyphenated form.
        try:
            canonical_id = str(UUID(command_id))
        except (ValueError, AttributeError, TypeError):
            raise InvalidInput("command_id")
        if canonical_id != command_id:
            raise InvalidInput("command_id")

        if operation not in _OPERATIONS:
            raise InvalidInput("operation")

        if not isinstance(payload, dict):
            raise InvalidInput("payload")
        try:
            encoded = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError):
            raise InvalidInput("payload")
        if len(encoded) > _MAX_PAYLOAD:
            raise InvalidInput("payload")
        payload_hash = hashlib.sha256(encoded).hexdigest()

        with self.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                signed = int.from_bytes(
                    hashlib.sha256(
                        f"subscription:{canonical_id}".encode()
                    ).digest()[:8],
                    "big",
                    signed=True,
                )
                conn.exec_driver_sql(
                    "SELECT pg_advisory_xact_lock(%s)", (signed,)
                )

            row = conn.execute(
                select(subscription_commands).where(
                    subscription_commands.c.command_id == canonical_id
                )
            ).mappings().first()
            if row is not None:
                if (
                    row["operation"] == operation
                    and row["payload_sha256"] == payload_hash
                ):
                    return copy.deepcopy(row["result"])
                raise Conflict("command id reused")

            result = apply_subscription(
                conn,
                operation,
                payload,
                key=key,
                public_origin=public_origin,
            )
            conn.execute(
                subscription_commands.insert().values(
                    command_id=canonical_id,
                    operation=operation,
                    payload_sha256=payload_hash,
                    result=result,
                    created_at=utcnow(),
                )
            )
            return copy.deepcopy(result)


__all__ = ["SubscriptionCommandMixin"]
