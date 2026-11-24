"""Explicit schema setup and readiness checks for townlight_comms.

This module never drops, renames, or migrates legacy prototype tables.  The
only managed objects are the townlight_comms schema, its contract tables, and
the schema_versions bookkeeping table.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Integer, MetaData, Table, inspect, select
from sqlalchemy.schema import CreateSchema

from . import subscription_models as subscription_models  # noqa: PLC0414 - required for shared metadata registration side effect
from .models import SCHEMA, metadata

SCHEMA_VERSION = 1
VERSION_TABLE = "schema_versions"

version_metadata = MetaData(schema=SCHEMA)
schema_versions = Table(
    VERSION_TABLE,
    version_metadata,
    Column("version", Integer, primary_key=True),
    Column("applied_at", DateTime(timezone=True), nullable=False),
)


class MigrationError(RuntimeError):
    """Base error for schema setup and readiness failures."""


class FutureVersionError(MigrationError):
    """The database was created by a newer townlight_comms version."""


def _bound(engine):
    if engine.dialect.name == "sqlite":
        translated = engine.execution_options(schema_translate_map={SCHEMA: None})
        return translated, None
    return engine, SCHEMA


def _versions(conn, schema):
    return [
        row[0]
        for row in conn.execute(
            select(schema_versions.c.version).order_by(schema_versions.c.version)
        )
    ]


def _reject_future(versions):
    future = [version for version in versions if version > SCHEMA_VERSION]
    if future:
        raise FutureVersionError(
            f"database schema version {max(future)} is newer than supported version {SCHEMA_VERSION}"
        )


def upgrade(engine):
    """Create the managed schema and tables, then record version 1.

    All work occurs in one transaction.  Existing future-version databases are
    rejected before any DDL is attempted.
    """

    bound, schema = _bound(engine)
    with bound.begin() as conn:
        inspector = inspect(conn)
        if inspector.has_table(VERSION_TABLE, schema=schema):
            existing = _versions(conn, schema)
            _reject_future(existing)
        else:
            existing = []

        if schema is not None:
            conn.execute(CreateSchema(schema, if_not_exists=True))

        metadata.create_all(conn)
        version_metadata.create_all(conn)

        current = _versions(conn, schema)
        if not current:
            conn.execute(
                schema_versions.insert().values(
                    version=SCHEMA_VERSION,
                    applied_at=datetime.now(UTC),
                )
            )
        elif SCHEMA_VERSION not in current:
            raise MigrationError("missing schema version 1")


def ready(engine):
    """Return True when all contract tables/columns and schema version 1 exist.

    The check is read-only.  A future schema version raises FutureVersionError.
    """

    bound, schema = _bound(engine)
    inspector = inspect(bound)

    versions = []
    if inspector.has_table(VERSION_TABLE, schema=schema):
        with bound.connect() as conn:
            versions = _versions(conn, schema)
        _reject_future(versions)

    expected = {
        table.name: {column.name for column in table.columns}
        for table in metadata.sorted_tables
    }
    expected[VERSION_TABLE] = {"version", "applied_at"}

    for name, columns in expected.items():
        if not inspector.has_table(name, schema=schema):
            return False
        actual = {
            column["name"]
            for column in inspector.get_columns(name, schema=schema)
        }
        if actual != columns:
            return False

    return versions == [SCHEMA_VERSION]


__all__ = [
    "SCHEMA_VERSION",
    "VERSION_TABLE",
    "FutureVersionError",
    "MigrationError",
    "ready",
    "upgrade",
]
