import hashlib

from sqlalchemy import select

from .models import approvals, articles, attachments
from .store import Conflict, InvalidInput, canonical_bytes, utcnow


class ApprovalMixin:
    def approve(self, article_id, expected_revision, expected_sha256, actor, verified_sources):
        if not isinstance(actor, str) or not actor or len(actor) > 128:
            raise InvalidInput("actor")
        if not isinstance(expected_sha256, str) or not expected_sha256:
            raise InvalidInput("expected_sha256")
        with self.engine.begin() as conn:
            art = self._article(conn, article_id, expected_revision, lock=True)
            if art["state"] != "review":
                raise Conflict("state")
            rev = self._revision(conn, article_id, art["current_revision"])
            if rev["sha256"] != expected_sha256:
                raise Conflict("sha256")
            sources = self._public_sources(verified_sources)
            if canonical_bytes(sources) != canonical_bytes(rev["source_refs"]):
                raise Conflict("sources")
            access = rev["access_review"]
            if (not isinstance(access, dict) or access.get("reviewed") is not True
                    or not isinstance(access.get("findings"), list)):
                raise Conflict("access_review")
            refs = rev["attachment_refs"] or []
            if not isinstance(refs, list):
                raise Conflict("attachments")
            for ref in refs:
                if not isinstance(ref, dict) or ref.get("public") is not True:
                    raise Conflict("attachment_public")
                attachment_id = ref.get("id")
                if attachment_id is None:
                    attachment_id = ref.get("attachment_id")
                if not isinstance(attachment_id, str) or not attachment_id:
                    raise Conflict("attachment")
                row = conn.execute(
                    select(attachments).where(
                        attachments.c.id == attachment_id,
                        attachments.c.article_id == article_id,
                    )
                ).first()
                if row is None:
                    raise Conflict("attachment")
                actual = row._mapping
                if (ref.get("name") != actual.get("name")
                        or ref.get("media_type") != actual.get("media_type")):
                    raise Conflict("attachment_manifest")
                blob = next((actual[k] for k in ("content", "data", "bytes", "blob")
                             if k in actual), None)
                if not isinstance(blob, (bytes, bytearray, memoryview)):
                    raise Conflict("attachment")
                digest = hashlib.sha256(blob).hexdigest()
                if len(blob) != ref.get("size") or digest != ref.get("sha256"):
                    raise Conflict("attachment_manifest")
            prior = conn.execute(
                select(approvals.c.sha256).where(
                    approvals.c.article_id == article_id,
                    approvals.c.revision == expected_revision,
                )
            ).scalar_one_or_none()
            if prior is None:
                conn.execute(approvals.insert().values(
                    article_id=article_id,
                    revision=expected_revision,
                    sha256=expected_sha256,
                    actor=actor,
                    created_at=utcnow(),
                ))
            elif prior != expected_sha256:
                raise Conflict("approval")
            res = conn.execute(
                articles.update().where(
                    articles.c.id == article_id,
                    articles.c.current_revision == expected_revision,
                    articles.c.state == "review",
                ).values(state="approved", updated_at=utcnow())
            )
            if res.rowcount != 1:
                raise Conflict("cas")
            self._event(conn, article_id, expected_revision, "approved", actor,
                        {"sha256": expected_sha256})
        return self.get(article_id)