"""External submission-attempt authority (FINK-G3B.0-R2).

Git is provenance, not authority: a `git checkout`, `reset`, branch switch or a
second clone can show an older registry in which a request was never
submitted. Whether a scientific fingerprint has been (or may have been)
submitted is therefore decided by one small SQLite database outside every
checkout, on the single host authorized to submit:

    $XDG_STATE_HOME/fink-lsst/submission_authority.sqlite3
    (default ~/.local/state/fink-lsst/submission_authority.sqlite3)

Rules:

- The scientific fingerprint is the primary key: one claim per fingerprint,
  taken atomically inside `BEGIN IMMEDIATE` before any Submit callback can be
  authorized. A claim is permanent; triggers refuse DELETE and any change to
  the bound identity, and batch id / topic are set once.
- One topic can belong to one fingerprint only.
- Every failure (missing permissions, a corrupt file, a foreign schema, a
  directory writable by others) raises `SubmissionAuthorityError`; callers
  treat that as "do not submit".
- Nothing secret is stored: fingerprints, digests, ids, statuses, timestamps.

Only one host is authorized to submit for this project, so this is a local
authority, not a distributed consensus service. Tests inject a temporary
path; there is no command-line option to point at another authority.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Mapping, Optional


AUTHORITY_SCHEMA_VERSION = "1"
AUTHORITY_DIRNAME = "fink-lsst"
AUTHORITY_FILENAME = "submission_authority.sqlite3"
STATUSES = frozenset(
    {
        "claimed",  # durable claim taken; nothing authorized yet
        "click_authorized",  # one-use submit authorization issued to the live browser context
        "submitted",  # portal returned a batch id (and possibly a topic)
        "uncertain",  # outcome not observed; never retried automatically
        "guard_blocked",  # the request guard refused the outgoing callback
        "job_found",  # batch/topic recovered by explicit reconciliation
        "negative_statement_recorded",  # an operator reported no job; the claim stays permanent
    }
)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS authority_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS submission_attempts (
    fingerprint TEXT PRIMARY KEY CHECK (length(fingerprint) = 64),
    attempt_id TEXT NOT NULL UNIQUE,
    acquisition_id TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    portal_config_sha256 TEXT NOT NULL,
    claimed_at_utc TEXT NOT NULL,
    code_revision TEXT NOT NULL,
    browser_context_id TEXT NOT NULL,
    status TEXT NOT NULL,
    batch_id TEXT,
    topic TEXT,
    updated_at_utc TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS submission_attempts_topic ON submission_attempts(topic) WHERE topic IS NOT NULL;
CREATE TABLE IF NOT EXISTS attempt_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL REFERENCES submission_attempts(fingerprint),
    attempt_id TEXT NOT NULL,
    at_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS submission_attempts_no_delete BEFORE DELETE ON submission_attempts
BEGIN SELECT RAISE(ABORT, 'submission attempts are permanent'); END;
CREATE TRIGGER IF NOT EXISTS attempt_events_no_delete BEFORE DELETE ON attempt_events
BEGIN SELECT RAISE(ABORT, 'attempt events are permanent'); END;
CREATE TRIGGER IF NOT EXISTS attempt_events_no_update BEFORE UPDATE ON attempt_events
BEGIN SELECT RAISE(ABORT, 'attempt events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS submission_attempts_identity_immutable
BEFORE UPDATE OF fingerprint, attempt_id, acquisition_id, request_sha256, portal_config_sha256, claimed_at_utc, code_revision, browser_context_id
ON submission_attempts
BEGIN SELECT RAISE(ABORT, 'submission attempt identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS submission_attempts_ids_set_once BEFORE UPDATE OF batch_id, topic ON submission_attempts
WHEN (OLD.batch_id IS NOT NULL AND NEW.batch_id IS NOT OLD.batch_id) OR (OLD.topic IS NOT NULL AND NEW.topic IS NOT OLD.topic)
BEGIN SELECT RAISE(ABORT, 'batch id and topic are set once'); END;
"""
_TABLES = {"authority_meta", "submission_attempts", "attempt_events"}


class SubmissionAuthorityError(RuntimeError):
    """The authority is unavailable, unreadable, insecure or inconsistent: do not submit."""


class AttemptAlreadyClaimedError(SubmissionAuthorityError):
    """The fingerprint already has a (permanent) submission attempt."""


class AuthorityConflictError(SubmissionAuthorityError):
    """An update contradicts what the authority already holds."""


@dataclass(frozen=True)
class SubmissionAttempt:
    fingerprint: str
    attempt_id: str
    acquisition_id: str
    request_sha256: str
    portal_config_sha256: str
    claimed_at_utc: str
    code_revision: str
    browser_context_id: str
    status: str
    batch_id: Optional[str]
    topic: Optional[str]
    updated_at_utc: str


def default_authority_path(environ: Optional[Mapping[str, str]] = None, home: Optional[Path] = None) -> Path:
    """`$XDG_STATE_HOME/fink-lsst/...` when set to an absolute path, else `~/.local/state/fink-lsst/...`."""
    env = os.environ if environ is None else environ
    state_home = env.get("XDG_STATE_HOME", "")
    base = Path(state_home) if state_home and Path(state_home).is_absolute() else (home or Path.home()) / ".local" / "state"
    return base / AUTHORITY_DIRNAME / AUTHORITY_FILENAME


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SubmissionAuthority:
    def __init__(self, path: Path, *, clock: Callable[[], str] = _utc_now):
        self.path = Path(path)
        self.clock = clock

    @classmethod
    def production(cls) -> "SubmissionAuthority":
        return cls(default_authority_path())

    # ------------------------------------------------------------ reads

    def get(self, fingerprint: str) -> Optional[SubmissionAttempt]:
        """The fingerprint's attempt, or None. A missing authority file means no claim exists yet."""
        if not self.path.exists() and not self.path.is_symlink():
            return None
        with self._connect(create=False) as connection:
            row = connection.execute("SELECT * FROM submission_attempts WHERE fingerprint = ?", (fingerprint,)).fetchone()
        return _attempt(row) if row else None

    def owner_of_topic(self, topic: str) -> Optional[str]:
        if not self.path.exists() and not self.path.is_symlink():
            return None
        with self._connect(create=False) as connection:
            row = connection.execute("SELECT fingerprint FROM submission_attempts WHERE topic = ?", (topic,)).fetchone()
        return row[0] if row else None

    def events(self, fingerprint: str) -> list[dict]:
        with self._connect(create=False) as connection:
            rows = connection.execute("SELECT at_utc, status, detail FROM attempt_events WHERE fingerprint = ? ORDER BY id", (fingerprint,)).fetchall()
        return [{"at_utc": row[0], "status": row[1], "detail": row[2]} for row in rows]

    # ------------------------------------------------------------ writes

    def claim(
        self,
        *,
        fingerprint: str,
        acquisition_id: str,
        request_sha256: str,
        portal_config_sha256: str,
        code_revision: str,
        browser_context_id: str,
    ) -> SubmissionAttempt:
        """Atomically take the permanent claim for `fingerprint`; raises if any claim exists."""
        now = self.clock()
        attempt_id = uuid.uuid4().hex
        with self._connect(create=True) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute("SELECT 1 FROM submission_attempts WHERE fingerprint = ?", (fingerprint,)).fetchone():
                    raise AttemptAlreadyClaimedError(f"fingerprint {fingerprint} already has a permanent submission attempt")
                connection.execute(
                    "INSERT INTO submission_attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'claimed', NULL, NULL, ?)",
                    (fingerprint, attempt_id, acquisition_id, request_sha256, portal_config_sha256, now, code_revision, browser_context_id, now),
                )
                connection.execute(
                    "INSERT INTO attempt_events (fingerprint, attempt_id, at_utc, status, detail) VALUES (?, ?, ?, 'claimed', ?)",
                    (fingerprint, attempt_id, now, f"claimed for {acquisition_id}"),
                )
                connection.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                connection.execute("ROLLBACK")
                raise AttemptAlreadyClaimedError(f"fingerprint {fingerprint} already has a permanent submission attempt") from exc
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        attempt = self.get(fingerprint)
        if attempt is None or attempt.attempt_id != attempt_id:
            raise SubmissionAuthorityError("claim was not durably recorded")
        return attempt

    def record_status(
        self,
        fingerprint: str,
        attempt_id: str,
        status: str,
        *,
        batch_id: Optional[str] = None,
        topic: Optional[str] = None,
        detail: str = "",
    ) -> SubmissionAttempt:
        """Append a status event; batch id and topic may be set once and must never conflict."""
        if status not in STATUSES or status == "claimed":
            raise AuthorityConflictError(f"unsupported status {status!r}")
        now = self.clock()
        with self._connect(create=False) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT * FROM submission_attempts WHERE fingerprint = ?", (fingerprint,)).fetchone()
                if row is None or row["attempt_id"] != attempt_id:
                    raise AuthorityConflictError(f"no submission attempt {attempt_id} for fingerprint {fingerprint}")
                for name, value in (("batch_id", batch_id), ("topic", topic)):
                    if value is not None and row[name] is not None and row[name] != value:
                        raise AuthorityConflictError(f"{name} {value!r} contradicts the recorded {row[name]!r}")
                if topic is not None:
                    owner = connection.execute("SELECT fingerprint FROM submission_attempts WHERE topic = ? AND fingerprint != ?", (topic, fingerprint)).fetchone()
                    if owner:
                        raise AuthorityConflictError(f"topic {topic} is already owned by another fingerprint ({owner[0][:12]}...)")
                connection.execute(
                    "UPDATE submission_attempts SET status = ?, batch_id = COALESCE(batch_id, ?), topic = COALESCE(topic, ?), updated_at_utc = ? WHERE fingerprint = ?",
                    (status, batch_id, topic, now, fingerprint),
                )
                connection.execute(
                    "INSERT INTO attempt_events (fingerprint, attempt_id, at_utc, status, detail) VALUES (?, ?, ?, ?, ?)",
                    (fingerprint, attempt_id, now, status, detail[:500]),
                )
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
        return self.get(fingerprint)

    # ------------------------------------------------------------ connection

    @contextmanager
    def _connect(self, *, create: bool) -> Iterator[sqlite3.Connection]:
        try:
            self._prepare(create)
            connection = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        except SubmissionAuthorityError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise SubmissionAuthorityError(f"submission authority {self.path} is unavailable: {exc}") from exc
        try:
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA busy_timeout = 30000")
                connection.execute("PRAGMA journal_mode = DELETE")
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute("PRAGMA foreign_keys = ON")
                check = connection.execute("PRAGMA quick_check").fetchone()
                if not check or check[0] != "ok":
                    raise SubmissionAuthorityError(f"submission authority {self.path} failed its integrity check")
                self._ensure_schema(connection, create)
            except sqlite3.DatabaseError as exc:
                raise SubmissionAuthorityError(f"submission authority {self.path} is unreadable or corrupt: {exc}") from exc
            try:
                yield connection
            except sqlite3.IntegrityError as exc:
                raise AuthorityConflictError(f"submission authority refused the change: {exc}") from exc
            except sqlite3.DatabaseError as exc:
                raise SubmissionAuthorityError(f"submission authority {self.path} failed: {exc}") from exc
        finally:
            connection.close()

    def _prepare(self, create: bool) -> None:
        parent = self.path.parent
        if create and not parent.exists():
            os.makedirs(parent, mode=0o700, exist_ok=True)
            os.chmod(parent, 0o700)
        info = os.stat(parent)
        if info.st_uid != os.getuid():
            raise SubmissionAuthorityError(f"authority directory {parent} is not owned by this user")
        if stat.S_IMODE(info.st_mode) & 0o022:
            raise SubmissionAuthorityError(f"authority directory {parent} is writable by others")
        if parent.name == AUTHORITY_DIRNAME and stat.S_IMODE(info.st_mode) & 0o077:
            os.chmod(parent, 0o700)
        if self.path.is_symlink():
            raise SubmissionAuthorityError(f"authority file {self.path} is a symlink")
        if not self.path.exists():
            if not create:
                raise SubmissionAuthorityError(f"submission authority {self.path} does not exist")
            try:
                os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            except FileExistsError:
                pass  # another process created it first; it is checked below like any existing file
        info = os.stat(self.path)
        if not stat.S_ISREG(info.st_mode):
            raise SubmissionAuthorityError(f"authority path {self.path} is not a regular file")
        if info.st_uid != os.getuid():
            raise SubmissionAuthorityError(f"authority file {self.path} is not owned by this user")
        if stat.S_IMODE(info.st_mode) & 0o077:
            os.chmod(self.path, 0o600)

    def _ensure_schema(self, connection: sqlite3.Connection, create: bool) -> None:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'sqlite_sequence'")}
        if not tables:
            if not create:
                raise SubmissionAuthorityError(f"submission authority {self.path} is empty")
            connection.execute("BEGIN IMMEDIATE")
            for statement in _SCHEMA.split(";\n"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute("INSERT OR IGNORE INTO authority_meta VALUES ('schema_version', ?)", (AUTHORITY_SCHEMA_VERSION,))
            connection.execute("COMMIT")
            tables = _TABLES
        if tables != _TABLES:
            raise SubmissionAuthorityError(f"submission authority {self.path} has an unexpected schema: {sorted(tables)}")
        version = connection.execute("SELECT value FROM authority_meta WHERE key = 'schema_version'").fetchone()
        if not version or version[0] != AUTHORITY_SCHEMA_VERSION:
            raise SubmissionAuthorityError(f"submission authority {self.path} has schema version {version[0] if version else None}")


def _attempt(row: sqlite3.Row) -> SubmissionAttempt:
    return SubmissionAttempt(**{key: row[key] for key in row.keys()})
