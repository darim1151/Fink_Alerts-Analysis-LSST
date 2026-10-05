"""Durable acquisition registry (layer D).

One directory per scientific request, named by its deterministic acquisition
id, under `configs/acquisitions/` by default (committed with the other
non-secret request provenance):

    <acquisition_id>/
        request.json        canonical request, written once
        portal_config.yml   compiled portal configuration, written once
        state_log.jsonl     append-only, hash-chained transition log
        evidence/           write-once evidence files (portal download, ...)

The current state is the replay of `state_log.jsonl`; it is never inferred
from which other files exist. Loading re-derives the request and portal
configuration from the scientific identity, re-validates every transition
and the hash chain, and fails on any discrepancy.

Appends take an exclusive lock on the log, re-read it, and refuse if another
writer appended since the caller loaded the record. A separate submission
lock (`.submission.lock`, never committed) serializes portal submissions.
Every evidence payload is scanned for credential-looking keys or values
before anything is written.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Optional, Sequence, Tuple

from fink_lsst.bulk_transfer.run_manifest import find_credential_like_entries
from fink_lsst.data_root import PathConfinementError, confine, validate_path_component

from .planner import AcquisitionRequest, PlanningError, canonical_json, request_from_dict
from .portal_config import compile_portal_config, portal_config_sha256, render_portal_yaml
from .states import AcquisitionState, StateLogEntry, TransitionError, check_transition


DEFAULT_REGISTRY_ROOT = Path("configs/acquisitions")
REQUEST_FILE = "request.json"
PORTAL_CONFIG_FILE = "portal_config.yml"
STATE_LOG_FILE = "state_log.jsonl"
EVIDENCE_DIR = "evidence"
SUBMISSION_LOCK_FILE = ".submission.lock"
# Markers beyond the run-manifest list that would indicate browser/session
# material or Kafka endpoint details.
EXTRA_SECRET_MARKERS = ("cookie", "storage_state", "authorization", "bearer", "private_key", "ssh-rsa", "ssh-ed25519", "bootstrap")
_ENDPOINT_PATTERNS = (
    re.compile(r"\bservers?\b", re.IGNORECASE),
    re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}:\d{2,5}\b", re.IGNORECASE),  # host:port
    re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"),  # IPv4 address
    re.compile(r":9092\b|:9093\b|:9094\b"),  # Kafka listener ports
)


class RegistryError(ValueError):
    """Raised when the registry is inconsistent or a write is refused."""


class DuplicateAcquisitionError(RegistryError):
    """Raised when a request with the same fingerprint is already registered."""


class ConcurrentModificationError(RegistryError):
    """Raised when the log changed after the caller loaded the record."""


class SubmissionLockedError(RegistryError):
    """Raised when another process holds this acquisition's submission lock."""


@dataclass(frozen=True)
class AcquisitionRecord:
    acquisition_id: str
    directory: Path
    request: AcquisitionRequest
    portal_yaml: str
    entries: Tuple[StateLogEntry, ...]

    @property
    def state(self) -> AcquisitionState:
        return self.entries[-1].to_state

    @property
    def fingerprint(self) -> str:
        return self.request.fingerprint

    @property
    def portal_config_sha256(self) -> str:
        return portal_config_sha256(self.portal_yaml)

    def last_entry(self, state: AcquisitionState) -> Optional[StateLogEntry]:
        return next((entry for entry in reversed(self.entries) if entry.to_state == state), None)

    @property
    def topic(self) -> Optional[str]:
        entry = self.last_entry(AcquisitionState.TOPIC_IDENTIFIED)
        return entry.evidence.get("topic") if entry else None

    @property
    def batch_id(self) -> Optional[str]:
        for entry in reversed(self.entries):
            if entry.evidence.get("batch_id"):
                return str(entry.evidence["batch_id"])
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AcquisitionRegistry:
    def __init__(self, root: Path = DEFAULT_REGISTRY_ROOT, clock: Callable[[], str] = _utc_now):
        self.root = Path(root)
        self.clock = clock

    # ------------------------------------------------------------ queries

    def path_for(self, acquisition_id: str) -> Path:
        validate_path_component(acquisition_id, "acquisition_id")
        return confine(self.root / acquisition_id, self.root.resolve()) if self.root.exists() else self.root / acquisition_id

    def list_records(self) -> list[AcquisitionRecord]:
        if not self.root.is_dir():
            return []
        return [self.load(path.name) for path in sorted(self.root.iterdir()) if path.is_dir() and not path.name.startswith(".")]

    def find_by_fingerprint(self, fingerprint: str) -> Optional[AcquisitionRecord]:
        matches = [record for record in self.list_records() if record.fingerprint == fingerprint]
        if len(matches) > 1:
            raise RegistryError(f"fingerprint {fingerprint} is registered more than once")
        return matches[0] if matches else None

    def load(self, acquisition_id: str) -> AcquisitionRecord:
        directory = self.path_for(acquisition_id)
        try:
            request = request_from_dict(json.loads((directory / REQUEST_FILE).read_text(encoding="utf-8")))
            portal_yaml = (directory / PORTAL_CONFIG_FILE).read_text(encoding="utf-8")
            lines = (directory / STATE_LOG_FILE).read_text(encoding="utf-8").splitlines()
        except (OSError, ValueError, PlanningError) as exc:
            raise RegistryError(f"acquisition {acquisition_id} is unreadable or inconsistent: {exc}") from exc
        if request.acquisition_id != acquisition_id:
            raise RegistryError(f"directory {acquisition_id} holds request {request.acquisition_id}")
        if portal_yaml != render_portal_yaml(compile_portal_config(request)):
            raise RegistryError(f"{acquisition_id}: portal_config.yml differs from the compiled canonical request")
        entries = _replay(lines, {"fingerprint": request.fingerprint, "portal_config_sha256": portal_config_sha256(portal_yaml)})
        if not entries:
            raise RegistryError(f"{acquisition_id}: empty state log")
        return AcquisitionRecord(acquisition_id, directory, request, portal_yaml, entries)

    # ------------------------------------------------------------ writes

    def create(self, request: AcquisitionRequest, portal_yaml: str, actor: str = "orchestrator") -> AcquisitionRecord:
        """Register a new request in state PLANNED; refuses an already registered fingerprint."""
        if portal_yaml != render_portal_yaml(compile_portal_config(request)):
            raise RegistryError("portal configuration must be compiled from the request")
        existing = self.find_by_fingerprint(request.fingerprint)
        if existing is not None:
            raise DuplicateAcquisitionError(f"request {request.fingerprint} is already registered as {existing.acquisition_id} ({existing.state.value})")
        payload = request.to_dict()
        _refuse_credentials(payload, "request")
        self.root.mkdir(parents=True, exist_ok=True)
        directory = self.path_for(request.acquisition_id)
        if directory.exists():
            raise DuplicateAcquisitionError(f"acquisition {request.acquisition_id} already exists")
        # Build the record in a hidden staging directory and rename it into
        # place, so a crash never leaves a half-written record behind.
        staging = self.root / f".staging-{request.acquisition_id}-{uuid.uuid4().hex[:8]}"
        staging.mkdir()
        try:
            request_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
            _write_new(staging / REQUEST_FILE, request_text)
            _write_new(staging / PORTAL_CONFIG_FILE, portal_yaml)
            entry = _seal(
                StateLogEntry(
                    seq=1,
                    at_utc=self.clock(),
                    from_state=None,
                    to_state=AcquisitionState.PLANNED,
                    actor=actor,
                    reason="request planned",
                    evidence={"fingerprint": request.fingerprint, "request_sha256": hashlib.sha256(request_text.encode("utf-8")).hexdigest()},
                ),
                None,
            )
            _write_new(staging / STATE_LOG_FILE, _entry_line(entry))
            try:
                os.rename(staging, directory)
            except OSError as exc:
                raise DuplicateAcquisitionError(f"acquisition {request.acquisition_id} already exists") from exc
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        return self.load(request.acquisition_id)

    def transition(
        self,
        record: AcquisitionRecord,
        target: AcquisitionState,
        *,
        actor: str,
        reason: str,
        evidence: Optional[Mapping[str, Any]] = None,
    ) -> AcquisitionRecord:
        """Validate and append one transition; returns the reloaded record."""
        evidence = dict(evidence or {})
        _refuse_credentials({"reason": reason, "evidence": evidence}, "transition")
        log_path = record.directory / STATE_LOG_FILE
        with open(log_path, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                current = self.load(record.acquisition_id)
                if current.entries[-1].entry_sha256 != record.entries[-1].entry_sha256:
                    raise ConcurrentModificationError(
                        f"{record.acquisition_id} changed since it was loaded (now {current.state.value}); reload before writing"
                    )
                try:
                    check_transition(current.entries, AcquisitionState(target), actor=actor, evidence=evidence, context=_context(current))
                except (KeyError, TypeError) as exc:
                    raise TransitionError(f"malformed evidence for {AcquisitionState(target).value}: {exc!r}") from exc
                last = current.entries[-1]
                entry = _seal(
                    StateLogEntry(
                        seq=last.seq + 1,
                        at_utc=self.clock(),
                        from_state=last.to_state,
                        to_state=AcquisitionState(target),
                        actor=actor,
                        reason=reason,
                        evidence=evidence,
                    ),
                    last.entry_sha256,
                )
                handle.seek(0, os.SEEK_END)
                handle.write(_entry_line(entry))
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return self.load(record.acquisition_id)

    def write_evidence_file(self, record: AcquisitionRecord, name: str, content: str) -> Path:
        """Write a new, never-overwritten evidence file after a credential scan."""
        try:
            validate_path_component(name, "evidence file name")
        except PathConfinementError as exc:
            raise RegistryError(str(exc)) from exc
        _refuse_credentials({"content": content}, f"evidence file {name}")
        directory = record.directory / EVIDENCE_DIR
        directory.mkdir(exist_ok=True)
        path = confine(directory / name, directory.resolve())
        try:
            _write_new(path, content)
        except FileExistsError as exc:
            raise RegistryError(f"evidence file {name} already exists; evidence is write-once") from exc
        return path

    def evidence_count(self, record: AcquisitionRecord, prefix: str) -> int:
        directory = record.directory / EVIDENCE_DIR
        return sum(1 for path in directory.iterdir() if path.name.startswith(prefix)) if directory.is_dir() else 0

    @contextmanager
    def submission_lock(self, acquisition_id: str) -> Iterator[None]:
        """Hold the exclusive, non-blocking submission lock for one acquisition."""
        path = self.path_for(acquisition_id) / SUBMISSION_LOCK_FILE
        handle = open(path, "a+", encoding="utf-8")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise SubmissionLockedError(f"another process is submitting {acquisition_id}") from exc
            yield
        finally:
            handle.close()


def _context(record: AcquisitionRecord) -> dict[str, Any]:
    return {"fingerprint": record.fingerprint, "portal_config_sha256": record.portal_config_sha256}


def _refuse_credentials(payload: Any, label: str) -> None:
    try:
        payload = json.loads(json.dumps(payload))  # tuples become lists; non-JSON values are refused
    except (TypeError, ValueError) as exc:
        raise RegistryError(f"{label} is not plain JSON data: {exc}") from exc
    found = find_credential_like_entries(payload)
    found += [path for path, text in _strings(payload) if any(marker in text.lower() for marker in EXTRA_SECRET_MARKERS)]
    found += [path for path in _keys(payload) if any(marker in path.lower() for marker in EXTRA_SECRET_MARKERS)]
    found += [path for path, text in _strings(payload) if any(pattern.search(text) for pattern in _ENDPOINT_PATTERNS)]
    if found:
        raise RegistryError(f"{label} contains credential-looking keys or values ({', '.join(sorted(set(found))[:5])}); nothing was written")


def _strings(value: Any, prefix: str = "") -> Iterator[Tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _strings(item, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _strings(item, f"{prefix}[{index}]")
    elif isinstance(value, str):
        yield prefix, value


def _keys(value: Any, prefix: str = "") -> Iterator[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield path
            yield from _keys(item, path)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _keys(item, f"{prefix}[{index}]")


def _entry_payload(entry: StateLogEntry) -> dict[str, Any]:
    return {
        "seq": entry.seq,
        "at_utc": entry.at_utc,
        "from": entry.from_state.value if entry.from_state else None,
        "to": entry.to_state.value,
        "actor": entry.actor,
        "reason": entry.reason,
        "evidence": dict(entry.evidence),
        "prev_entry_sha256": entry.prev_entry_sha256,
    }


def _seal(entry: StateLogEntry, prev_sha: Optional[str]) -> StateLogEntry:
    entry = replace(entry, prev_entry_sha256=prev_sha)
    return replace(entry, entry_sha256=hashlib.sha256(canonical_json(_entry_payload(entry)).encode("utf-8")).hexdigest())


def _entry_line(entry: StateLogEntry) -> str:
    payload = _entry_payload(entry)
    payload["entry_sha256"] = entry.entry_sha256
    return json.dumps(payload, sort_keys=True, ensure_ascii=True) + "\n"


def _replay(lines: Sequence[str], context: Mapping[str, Any]) -> Tuple[StateLogEntry, ...]:
    entries: list[StateLogEntry] = []
    prev_sha = None
    for number, line in enumerate(lines, start=1):
        try:
            payload = json.loads(line)
            entry = StateLogEntry(
                seq=int(payload["seq"]),
                at_utc=str(payload["at_utc"]),
                from_state=AcquisitionState(payload["from"]) if payload["from"] else None,
                to_state=AcquisitionState(payload["to"]),
                actor=str(payload["actor"]),
                reason=str(payload["reason"]),
                evidence=dict(payload.get("evidence") or {}),
                prev_entry_sha256=payload.get("prev_entry_sha256"),
                entry_sha256=str(payload["entry_sha256"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RegistryError(f"state log line {number} is malformed: {exc}") from exc
        if entry.seq != number or entry.prev_entry_sha256 != prev_sha:
            raise RegistryError(f"state log line {number} breaks the sequence or hash chain")
        if _seal(entry, prev_sha).entry_sha256 != entry.entry_sha256:
            raise RegistryError(f"state log line {number} was modified after it was written")
        if entry.from_state != (entries[-1].to_state if entries else None):
            raise RegistryError(f"state log line {number} does not start from the previous state")
        try:
            check_transition(entries, entry.to_state, actor=entry.actor, evidence=entry.evidence, context=context)
        except (TransitionError, KeyError, TypeError, ValueError) as exc:
            raise RegistryError(f"state log line {number} records an invalid transition: {exc!r}") from exc
        entries.append(entry)
        prev_sha = entry.entry_sha256
    return tuple(entries)


def _write_new(path: Path, content: str) -> None:
    """Create `path` exclusively and fsync it; never overwrites."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def registry_durability_problems(directory: Path) -> list[str]:
    """Reasons a Git checkout could silently roll this record back; empty when it is safe.

    The duplicate-submission policy lives in the committed log, so before any
    live submission the record must be committed, clean and pushed: otherwise
    a `git checkout`/`reset`, a stale clone or a non-editable install would
    show an older state and could permit a second job.
    """
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True, check=False)

    if git("rev-parse", "--show-toplevel").returncode != 0:
        return ["the registry is not inside a Git checkout"]
    problems = []
    if git("ls-files", "--error-unmatch", STATE_LOG_FILE).returncode != 0:
        problems.append("the record is not committed")
    if git("status", "--porcelain", "--", ".").stdout.strip():
        problems.append("the record has uncommitted changes")
    if git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").returncode != 0:
        problems.append("the branch has no upstream; push the record first")
    elif git("rev-list", "@{u}..HEAD", "--", ".").stdout.strip():
        problems.append("the record has unpushed commits")
    return problems
