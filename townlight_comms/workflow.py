"""Composed communications repository."""

from __future__ import annotations

from .approval import ApprovalMixin
from .attachments import AttachmentMixin
from .editorial import EditorialMixin
from .email_claims import EmailClaimsMixin
from .email_enqueue import EmailEnqueueMixin
from .public_artifact import ArtifactMixin
from .publication_claims import PublicationClaimsMixin
from .publication_reconciliation import PublicationReconciliationMixin
from .publication_results import PublicationResultsMixin
from .records_archive import RecordsArchiveMixin
from .records_guard import RecordsGuardMixin
from .scheduling import SchedulingMixin
from .source_snapshots import SourceSnapshotMixin
from .store import Conflict, InvalidInput, NotFound, RepositoryBase, Unauthorized
from .subscription_commands import SubscriptionCommandMixin
from .subscription_delivery import SubscriptionDeliveryMixin
from .subscriptions import SubscriptionMixin


class CommunicationsRepository(
    EditorialMixin,
    ApprovalMixin,
    SchedulingMixin,
    SubscriptionMixin,
    SubscriptionCommandMixin,
    SubscriptionDeliveryMixin,
    PublicationClaimsMixin,
    PublicationResultsMixin,
    PublicationReconciliationMixin,
    EmailEnqueueMixin,
    EmailClaimsMixin,
    AttachmentMixin,
    ArtifactMixin,
    SourceSnapshotMixin,
    RecordsArchiveMixin,
    RecordsGuardMixin,
    RepositoryBase,
):
    pass


__all__ = [
    "ApprovalMixin",
    "CommunicationsRepository",
    "Conflict",
    "EditorialMixin",
    "EmailClaimsMixin",
    "EmailEnqueueMixin",
    "InvalidInput",
    "NotFound",
    "RepositoryBase",
    "SourceSnapshotMixin",
    "Unauthorized",
]
