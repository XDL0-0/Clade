"""SQLite coordination boundary shared by world commits and narrative jobs."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS worlds (
 world_id TEXT PRIMARY KEY, seed INTEGER NOT NULL, manifest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commits (
 commit_id TEXT PRIMARY KEY, world_id TEXT NOT NULL REFERENCES worlds(world_id),
 timeline_id TEXT NOT NULL, generation INTEGER NOT NULL, revision INTEGER NOT NULL,
 turn_id INTEGER NOT NULL, parent_id TEXT REFERENCES commits(commit_id),
 base_hash TEXT, state_hash TEXT NOT NULL, payload TEXT NOT NULL,
 is_checkpoint INTEGER NOT NULL, command_key TEXT, record_hash TEXT NOT NULL,
 UNIQUE(world_id, timeline_id, generation, revision)
);
CREATE TABLE IF NOT EXISTS timelines (
 world_id TEXT NOT NULL REFERENCES worlds(world_id), timeline_id TEXT NOT NULL,
 head_id TEXT NOT NULL REFERENCES commits(commit_id), fork_id TEXT REFERENCES commits(commit_id),
 PRIMARY KEY(world_id, timeline_id)
);
CREATE TABLE IF NOT EXISTS commands (
 world_id TEXT NOT NULL, timeline_id TEXT NOT NULL, generation INTEGER NOT NULL,
 command_key TEXT NOT NULL, input_hash TEXT NOT NULL, input_payload TEXT NOT NULL,
 commit_id TEXT NOT NULL REFERENCES commits(commit_id),
 PRIMARY KEY(world_id, timeline_id, generation, command_key)
);
CREATE TABLE IF NOT EXISTS turn_boundaries (
 world_id TEXT NOT NULL, timeline_id TEXT NOT NULL, generation INTEGER NOT NULL,
 turn_id INTEGER NOT NULL, commit_id TEXT NOT NULL REFERENCES commits(commit_id),
 PRIMARY KEY(world_id, timeline_id, generation, turn_id)
);
CREATE TABLE IF NOT EXISTS events (
 event_id TEXT PRIMARY KEY, commit_id TEXT NOT NULL REFERENCES commits(commit_id),
 ordinal INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(commit_id, ordinal)
);
CREATE TABLE IF NOT EXISTS outbox (
 cursor INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE,
 world_id TEXT NOT NULL, timeline_id TEXT NOT NULL,
 commit_id TEXT NOT NULL REFERENCES commits(commit_id), kind TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS outbox_scope ON outbox(world_id, timeline_id, cursor);
CREATE TABLE IF NOT EXISTS ai_jobs (
 job_id TEXT PRIMARY KEY, world_id TEXT NOT NULL, timeline_id TEXT NOT NULL,
 generation INTEGER NOT NULL, idempotency_key TEXT NOT NULL, input_hash TEXT NOT NULL,
 status TEXT NOT NULL, lease_until REAL, created_at REAL NOT NULL, payload TEXT NOT NULL,
 UNIQUE(world_id, timeline_id, generation, idempotency_key)
);
CREATE INDEX IF NOT EXISTS jobs_claim ON ai_jobs(status, lease_until, created_at);
CREATE TABLE IF NOT EXISTS narrative_heads (
 world_id TEXT NOT NULL, timeline_id TEXT NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(world_id, timeline_id)
);
CREATE TABLE IF NOT EXISTS annotations (
 job_id TEXT PRIMARY KEY REFERENCES ai_jobs(job_id),
 commit_id TEXT NOT NULL REFERENCES commits(commit_id),
 narrative_revision INTEGER NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stage_runs (
 commit_id TEXT NOT NULL REFERENCES commits(commit_id), stage_name TEXT NOT NULL,
 payload TEXT NOT NULL, PRIMARY KEY(commit_id, stage_name)
);
CREATE TABLE IF NOT EXISTS turn_metrics (
 commit_id TEXT PRIMARY KEY REFERENCES commits(commit_id), payload TEXT NOT NULL
);
"""


class StorageCorruption(ValueError):
    """A committed record, object or hash chain failed validation."""


class IdempotencyConflict(ValueError):
    """A scoped idempotency key was reused for a different request."""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in (0, 2):
                raise StorageCorruption(f"Unsupported database schema {version}")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
            connection.execute("PRAGMA user_version=2")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def backup(self, target: Path) -> None:
        """Include WAL via the SQLite backup API, never raw-copy a live database."""
        if target.resolve() == self.path.resolve():
            raise ValueError("Backup target must differ from live database")
        with self.connection() as source, sqlite3.connect(target) as destination:
            source.backup(destination)
