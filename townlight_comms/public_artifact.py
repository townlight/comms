from __future__ import annotations

import io
import zipfile

from sqlalchemy import select

from .models import approvals, attachments, revisions
from .store import Conflict, InvalidInput, canonical_bytes, digest

_HEX = set("0123456789abcdef")


class ArtifactMixin:
    def build_public_artifact(self, article_id, revision, expected_sha256, source_snapshots):
        article_id = self._bounded(article_id, "article_id", 256)
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise InvalidInput("revision")
        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(ch not in _HEX for ch in expected_sha256)
        ):
            raise InvalidInput("expected_sha256")

        with self.engine.connect() as conn:
            revision_row = conn.execute(
                select(revisions).where(
                    revisions.c.article_id == article_id,
                    revisions.c.revision == revision,
                )
            ).mappings().first()
            if revision_row is None:
                raise Conflict("revision")
            rev = dict(revision_row)

            approval = conn.execute(
                select(approvals).where(
                    approvals.c.article_id == article_id,
                    approvals.c.revision == revision,
                    approvals.c.sha256 == expected_sha256,
                )
            ).mappings().first()
            if approval is None:
                raise Conflict("approval")

            try:
                sources = self._public_sources(rev.get("source_refs"))
                refs = self._attachment_refs(rev.get("attachment_refs"))
            except InvalidInput:
                raise Conflict("revision manifest") from None

            if not isinstance(source_snapshots, list) or len(source_snapshots) != len(sources):
                raise InvalidInput("source_snapshots")
            snapshots = []
            total_snapshot_bytes = 0
            for snapshot, source in zip(source_snapshots, sources):
                if (
                    not isinstance(snapshot, dict)
                    or set(snapshot) != {"reference", "content"}
                    or snapshot["reference"] != source
                ):
                    raise InvalidInput("source_snapshots")
                snapshot_bytes = canonical_bytes(snapshot["content"])
                if len(snapshot_bytes) > 1024 * 1024:
                    raise InvalidInput("source_snapshots")
                total_snapshot_bytes += len(snapshot_bytes)
                if total_snapshot_bytes > 5 * 1024 * 1024:
                    raise InvalidInput("source_snapshots")
                if digest(snapshot_bytes) != source.get("sha256"):
                    raise InvalidInput("source_snapshots")
                snapshots.append(
                    {"reference": source, "content": snapshot["content"]}
                )

            content = {
                "title": rev.get("title"),
                "body": rev.get("body"),
                "language": rev.get("language"),
                "source_refs": sources,
                "access_review": rev.get("access_review"),
                "translation_provenance": rev.get("translation_provenance"),
                "attachment_refs": refs,
            }
            actual_sha256 = digest(canonical_bytes(content))
            if (
                rev.get("sha256") != expected_sha256
                or actual_sha256 != expected_sha256
            ):
                raise Conflict("sha256")

            files = []
            for ref in sorted(refs, key=lambda item: item["id"]):
                if ref.get("public") is not True:
                    raise Conflict("attachment_public")
                row = conn.execute(
                    select(attachments).where(
                        attachments.c.id == ref["id"],
                        attachments.c.article_id == article_id,
                    )
                ).mappings().first()
                if row is None:
                    raise Conflict("attachment")
                row = dict(row)
                data = row.get("content")
                if not isinstance(data, (bytes, bytearray, memoryview)):
                    raise Conflict("attachment")
                data = bytes(data)
                if (
                    row.get("public") is not True
                    or row.get("name") != ref["name"]
                    or row.get("media_type") != ref["media_type"]
                    or row.get("size") != ref["size"]
                    or row.get("sha256") != ref["sha256"]
                    or len(data) != ref["size"]
                    or digest(data) != ref["sha256"]
                ):
                    raise Conflict("attachment_manifest")
                files.append((f"attachments/{ref['id']}", data))

        payload = {
            "schema_version": 1,
            "article_id": article_id,
            "revision": revision,
            "title": content["title"],
            "body": content["body"],
            "language": content["language"],
            "source_refs": content["source_refs"],
            "source_snapshots": snapshots,
            "access_review": content["access_review"],
            "translation_provenance": content["translation_provenance"],
            "attachment_refs": content["attachment_refs"],
            "sha256": actual_sha256,
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(
            buffer, "w", compression=zipfile.ZIP_STORED, allowZip64=False
        ) as archive:
            for name, data in [("article.json", canonical_bytes(payload))] + files:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 0
                info.external_attr = 0
                archive.writestr(info, data)
        return buffer.getvalue()
