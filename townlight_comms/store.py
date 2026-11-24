from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import PureWindowsPath
from uuid import UUID

from sqlalchemy import inspect, select
from sqlalchemy.schema import CreateSchema

from .models import (
    SCHEMA,
    SOURCE_KINDS,
    articles,
    events,
    metadata,
    publications,
    revisions,
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_KEYS = {"kind", "id", "revision", "title", "url", "sha256", "eligible"}
_ATTACHMENT_KEYS = {"id", "name", "media_type", "size", "sha256", "public"}
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}
_MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024
_MAX_ATTACHMENTS_TOTAL = 20 * 1024 * 1024


class InvalidInput(ValueError):
    pass


class Conflict(RuntimeError):
    pass


class NotFound(LookupError):
    pass


class Unauthorized(PermissionError):
    pass


def utcnow():
    return datetime.now(UTC)


def new_id():
    return str(uuid.uuid4())


def canonical_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(value):
    return hashlib.sha256(value).hexdigest()


class RepositoryBase:
    def __init__(self, engine, bootstrap=False):
        if engine.dialect.name == "sqlite":
            self.engine = engine.execution_options(
                schema_translate_map={SCHEMA: None}
            )
        else:
            self.engine = engine
        self.schema = None if engine.dialect.name == "sqlite" else SCHEMA
        if bootstrap:
            if self.schema:
                with self.engine.begin() as conn:
                    conn.execute(CreateSchema(self.schema, if_not_exists=True))
            metadata.create_all(self.engine)
        else:
            inspector = inspect(self.engine)
            for table in metadata.sorted_tables:
                if not inspector.has_table(table.name, schema=self.schema):
                    raise RuntimeError(f"missing table: {table.name}")

    @staticmethod
    def _dict(row):
        return None if row is None else dict(row._mapping)

    def _article(self, conn, article_id, expected_revision=None, lock=False):
        stmt = select(articles).where(articles.c.id == article_id)
        if lock:
            stmt = stmt.with_for_update()
        row = conn.execute(stmt).mappings().first()
        if row is None:
            raise NotFound(article_id)
        if expected_revision is not None:
            current = row.get("current_revision")
            if current is None:
                current = row.get("revision")
            if current != expected_revision:
                raise Conflict("revision mismatch")
        return dict(row)

    def _revision(self, conn, article_id, revision=None):
        stmt = select(revisions).where(revisions.c.article_id == article_id)
        if revision is None:
            stmt = stmt.order_by(revisions.c.revision.desc())
        else:
            stmt = stmt.where(revisions.c.revision == revision)
        row = conn.execute(stmt.limit(1)).mappings().first()
        if row is None:
            raise NotFound(f"revision {revision}")
        return dict(row)

    def _event(self, conn, article_id, revision, kind, actor, payload):
        conn.execute(
            events.insert().values(
                article_id=article_id,
                revision=revision,
                kind=kind,
                actor=actor,
                payload=payload,
                created_at=utcnow(),
            )
        )

    @staticmethod
    def _bounded(value, name, maximum):
        if not isinstance(value, str) or not value or len(value) > maximum:
            raise InvalidInput(name)
        return value

    def _public_sources(self, refs):
        if not isinstance(refs, list) or not 0 <= len(refs) <= 50:
            raise InvalidInput("sources")
        result = []
        for source in refs:
            if not isinstance(source, dict) or set(source) != _SOURCE_KEYS:
                raise InvalidInput("source keys")
            kind = source["kind"]
            if kind not in SOURCE_KINDS:
                raise InvalidInput("source kind")
            item = {
                "kind": kind,
                "id": self._bounded(source["id"], "source id", 256),
                "revision": self._bounded(source["revision"], "source revision", 256),
                "title": self._bounded(source["title"], "source title", 512),
                "url": self._bounded(source["url"], "source url", 2048),
                "sha256": source["sha256"],
                "eligible": source["eligible"],
            }
            url = item["url"]
            if not (
                url.startswith("https://")
                or (url.startswith("/") and not url.startswith("//"))
            ):
                raise InvalidInput("source url")
            if not isinstance(item["sha256"], str) or not _HEX64.fullmatch(item["sha256"]):
                raise InvalidInput("source sha256")
            if item["eligible"] is not True:
                raise InvalidInput("source eligible")
            result.append(item)
        return result

    @staticmethod
    def _attachment_name(value):
        if not isinstance(value, str) or not value or len(value) > 150:
            raise InvalidInput("attachment name")
        if any(ch in '<>:"/\\|?*' or ord(ch) < 32 for ch in value):
            raise InvalidInput("attachment name")
        if value in {".", ".."} or value.endswith((" ", ".")):
            raise InvalidInput("attachment name")
        if PureWindowsPath(value).name != value:
            raise InvalidInput("attachment name")
        if value.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
            raise InvalidInput("attachment name")
        return value

    def _attachment_refs(self, refs):
        if refs is None:
            return []
        if not isinstance(refs, list) or len(refs) > 20:
            raise InvalidInput("attachment_refs")
        result = []
        seen = set()
        total = 0
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != _ATTACHMENT_KEYS:
                raise InvalidInput("attachment_ref keys")
            attachment_id = ref["id"]
            if not isinstance(attachment_id, str):
                raise InvalidInput("attachment id")
            try:
                UUID(attachment_id)
            except (ValueError, AttributeError):
                raise InvalidInput("attachment id")
            if attachment_id in seen:
                raise InvalidInput("attachment id")
            seen.add(attachment_id)
            name = self._attachment_name(ref["name"])
            media_type = self._bounded(
                ref["media_type"], "attachment media_type", 128
            )
            size = ref["size"]
            if (
                isinstance(size, bool)
                or not isinstance(size, int)
                or not 1 <= size <= _MAX_ATTACHMENT_SIZE
            ):
                raise InvalidInput("attachment size")
            sha256 = ref["sha256"]
            if not isinstance(sha256, str) or not _HEX64.fullmatch(sha256):
                raise InvalidInput("attachment sha256")
            public = ref["public"]
            if not isinstance(public, bool):
                raise InvalidInput("attachment public")
            total += size
            if total > _MAX_ATTACHMENTS_TOTAL:
                raise InvalidInput("attachment total size")
            result.append(
                {
                    "id": attachment_id,
                    "name": name,
                    "media_type": media_type,
                    "size": size,
                    "sha256": sha256,
                    "public": public,
                }
            )
        return result

    def _content(
        self,
        title,
        body,
        language,
        sources,
        access_review=None,
        translation_provenance=None,
        attachment_refs=None,
    ):
        title = self._bounded(title, "title", 512)
        if not isinstance(body, str) or not body.strip() or len(body) > 100000:
            raise InvalidInput("body")
        language = self._bounded(language, "language", 16)
        source_refs = self._public_sources(sources)
        access_review = {} if access_review is None else access_review
        translation_provenance = (
            {} if translation_provenance is None else translation_provenance
        )
        if not isinstance(access_review, dict) or not isinstance(
            translation_provenance, dict
        ):
            raise InvalidInput("content metadata")
        if len(canonical_bytes(access_review)) > 100000:
            raise InvalidInput("access review")
        if len(canonical_bytes(translation_provenance)) > 100000:
            raise InvalidInput("translation provenance")
        attachment_refs = self._attachment_refs(attachment_refs)
        content = {
            "title": title,
            "body": body,
            "language": language,
            "source_refs": source_refs,
            "access_review": access_review,
            "translation_provenance": translation_provenance,
            "attachment_refs": attachment_refs,
        }
        content["sha256"] = digest(canonical_bytes(content))
        return content

    def get(self, article_id):
        with self.engine.connect() as conn:
            article = self._article(conn, article_id)
            current = article.get("current_revision")
            if current is None:
                current = article.get("revision")
            try:
                revision = self._revision(conn, article_id, current)
            except NotFound:
                revision = None
            history = [
                dict(row._mapping)
                for row in conn.execute(
                    select(publications)
                    .where(publications.c.article_id == article_id)
                    .order_by(publications.c.created_at)
                )
            ]
            event_rows = [
                dict(row._mapping)
                for row in conn.execute(
                    select(events)
                    .where(events.c.article_id == article_id)
                    .order_by(events.c.created_at)
                )
            ]
            article["revision"] = revision
            article["history"] = history
            article["events"] = event_rows
            return article

    def list_articles(self, state=None, limit=50, offset=0):
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 200
        ):
            raise InvalidInput("limit")
        if (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or not 0 <= offset <= 100000
        ):
            raise InvalidInput("offset")
        stmt = select(articles)
        if state is not None:
            stmt = stmt.where(articles.c.state == state)
        stmt = stmt.order_by(articles.c.id).limit(limit).offset(offset)
        with self.engine.connect() as conn:
            return [dict(row._mapping) for row in conn.execute(stmt)]
