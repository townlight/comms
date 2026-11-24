from __future__ import annotations

import io
import zipfile

from sqlalchemy import select

from .models import publications
from .records_guard import is_publication_exposed
from .store import Conflict, canonical_bytes

_RECEIPTS_LIMIT = 100
_ARCHIVE_LIMIT = 40 * 1024 * 1024

def _iso(value):
    return None if value is None else (value.isoformat() if hasattr(value, "isoformat") else value)
def _counts(value):
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items() if isinstance(v, int) and not isinstance(v, bool)}
class RecordsArchiveMixin:
    def build_records_archive(self, article_id, revision, expected_sha256):
        article_id = self._bounded(article_id, "article_id", 256)
        snapshots = self.get_source_snapshots(article_id, revision, expected_sha256)
        public_artifact = self.build_public_artifact(article_id, revision, expected_sha256, snapshots)
        with self.engine.connect() as conn:
            publication_rows = conn.execute(
                select(publications).where(
                    publications.c.article_id == article_id,
                    publications.c.revision == revision,
                    publications.c.action.in_(("publish", "correct")),
                ).order_by(publications.c.created_at, publications.c.id).limit(_RECEIPTS_LIMIT + 1)
            ).mappings().all()
            exposed = [row for row in publication_rows if is_publication_exposed(row)]
            if not exposed or len(exposed) > _RECEIPTS_LIMIT:
                raise Conflict("publication_receipts")
            receipts = [{
                "action": row["action"],
                "due_at": _iso(row["due_at"]),
                "state": row["state"],
                "created_at": _iso(row["created_at"]),
                "updated_at": _iso(row["updated_at"]),
                "actor": row["actor"],
                "website_receipt": row["website_receipt"],
                "email_summary": _counts(row["email_summary"]),
            } for row in exposed]
        receipt_doc = {
            "article_id": article_id,
            "revision": revision,
            "content_sha256": expected_sha256,
            "publication_receipts": receipts,
        }
        files = [
            ("published-content.zip", public_artifact),
            ("publication-receipts.json", canonical_bytes(receipt_doc)),
        ]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
            for name, data in files:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 0
                info.external_attr = 0
                archive.writestr(info, data)
        result = buffer.getvalue()
        if len(result) > _ARCHIVE_LIMIT:
            raise Conflict("archive_size")
        return result
