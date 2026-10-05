"""Typed evidence receipts and verified evidence references (FINK-G3B.0-R2).

Equal counts do not prove provenance. Each evidence step after topic
identification is a versioned receipt whose identity fields are taken from
the `AcquisitionRecord`, never from the caller:

    fingerprint -> acquisition id -> batch id -> topic
      -> topic-metadata receipt (topic actually queried, watermarks)
      -> transfer receipt (attempt id, confined raw directory, command digest)
      -> delivery receipt (the same raw directory, inventory, reconciliation)

Receipts are stored as write-once evidence files and referenced from the
state log as `{"path": "evidence/<file>", "sha256": ..., "kind": ...}`. On
every load the registry re-reads each referenced file, refuses missing files,
symlinks, paths outside the record and digest mismatches, and requires the
file to equal the receipt logged with the transition.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence, Tuple

from fink_lsst.bulk_transfer.raw_audit import build_raw_audit
from fink_lsst.data_root import PathConfinementError, confine, confine_tree, storage_base

from .evidence import build_raw_inventory, reconcile_delivery, summarize_inventory
from .states import AcquisitionState

if TYPE_CHECKING:  # pragma: no cover
    from .registry import AcquisitionRecord


RECEIPT_SCHEMA_VERSION = 1
EVIDENCE_KINDS = frozenset({"portal_download", "topic_metadata", "transfer", "delivery", "inventory_summary"})
_EVIDENCE_PATH = re.compile(r"evidence/[A-Za-z0-9][A-Za-z0-9._-]{0,254}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


class EvidenceError(ValueError):
    """Evidence is missing, altered, misplaced or bound to something else."""


@dataclass(frozen=True)
class PartitionWatermarks:
    partition: int
    low: int
    high: int

    def to_dict(self) -> dict[str, int]:
        return {"partition": self.partition, "low": self.low, "high": self.high}


@dataclass(frozen=True)
class TopicWatermarkObservation:
    """Watermarks read from Kafka for the topic named here (the topic actually queried)."""

    topic: str
    checked_utc: str
    partitions: Tuple[PartitionWatermarks, ...]


def expected_topic_messages(partitions: Sequence[PartitionWatermarks]) -> int:
    """Return sum(high - low) over all partitions after sanity checks."""
    if not partitions:
        raise EvidenceError("no partitions were reported for the topic")
    seen = set()
    total = 0
    for item in partitions:
        if item.partition in seen:
            raise EvidenceError(f"partition {item.partition} reported twice")
        seen.add(item.partition)
        if item.low < 0 or item.high < item.low:
            raise EvidenceError(f"partition {item.partition} has invalid watermarks low={item.low} high={item.high}")
        total += item.high - item.low
    return total


# ---------------------------------------------------------------- evidence references


def evidence_ref(name: str, content: bytes, kind: str) -> dict[str, str]:
    if kind not in EVIDENCE_KINDS:
        raise EvidenceError(f"unknown evidence kind {kind!r}")
    return {"path": f"evidence/{name}", "sha256": hashlib.sha256(content).hexdigest(), "kind": kind}


def verify_evidence_ref(record_dir: Path, ref: Any, *, kind: str) -> bytes:
    """Return the bytes of a referenced evidence file after every integrity check."""
    if not isinstance(ref, Mapping) or set(ref) != {"path", "sha256", "kind"}:
        raise EvidenceError(f"malformed evidence reference {ref!r}")
    if ref["kind"] != kind:
        raise EvidenceError(f"evidence {ref['path']!r} is {ref['kind']!r}, expected {kind!r}")
    path_text, digest = str(ref["path"]), str(ref["sha256"])
    if not _EVIDENCE_PATH.fullmatch(path_text) or not _HEX64.fullmatch(digest):
        raise EvidenceError(f"evidence reference {path_text!r} is not a confined evidence path with a sha256")
    record_dir = Path(record_dir)
    evidence_dir = record_dir / "evidence"
    if evidence_dir.is_symlink() or not evidence_dir.is_dir():
        raise EvidenceError(f"evidence directory of {record_dir.name} is missing or redirected")
    target = evidence_dir / path_text.split("/", 1)[1]
    try:
        info = os.lstat(target)
    except FileNotFoundError as exc:
        raise EvidenceError(f"referenced evidence {path_text} is missing") from exc
    if not stat.S_ISREG(info.st_mode):
        raise EvidenceError(f"referenced evidence {path_text} is not a regular file (symlinks are refused)")
    try:
        confine(target, record_dir.resolve())
    except PathConfinementError as exc:
        raise EvidenceError(f"referenced evidence {path_text} escapes its record") from exc
    content = target.read_bytes()
    if hashlib.sha256(content).hexdigest() != digest:
        raise EvidenceError(f"referenced evidence {path_text} sha256 does not match its recorded digest")
    return content


def receipt_text(receipt: Mapping[str, Any]) -> str:
    return json.dumps(receipt, indent=2, sort_keys=True) + "\n"


# ---------------------------------------------------------------- receipt builders


def _identity(record: "AcquisitionRecord") -> dict[str, Any]:
    topic = record.topic
    if not topic:
        raise EvidenceError(f"{record.acquisition_id} has no identified topic")
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "acquisition_id": record.acquisition_id,
        "fingerprint": record.fingerprint,
        "batch_id": record.batch_id,
        "topic": topic,
    }


def build_topic_metadata_receipt(record: "AcquisitionRecord", observation: TopicWatermarkObservation) -> dict[str, Any]:
    identity = _identity(record)
    if observation.topic != identity["topic"]:
        raise EvidenceError(f"queried topic {observation.topic} is not this acquisition's topic {identity['topic']}")
    partitions = sorted(observation.partitions, key=lambda item: item.partition)
    return {
        **identity,
        "kind": "topic_metadata",
        "queried_topic": observation.topic,
        "checked_utc": observation.checked_utc,
        "partitions": [item.to_dict() for item in partitions],
        "expected_topic_messages": expected_topic_messages(partitions),
        "meaning": "sum(high - low) of the fresh topic; transport expectation, not scientific completeness",
    }


def build_transfer_receipt(record: "AcquisitionRecord", *, exit_code: int, terminal_committed: int, terminal_lag: int, log_sha256: Any = None) -> dict[str, Any]:
    running = record.entries[-1]
    if running.to_state != AcquisitionState.TRANSFER_RUNNING:
        raise EvidenceError(f"{record.acquisition_id} is {record.state.value}; a transfer receipt needs TRANSFER_RUNNING")
    identity = _identity(record)
    return {
        **identity,
        "kind": "transfer",
        "transfer_attempt_id": running.evidence["transfer_attempt_id"],
        "raw_dir": running.evidence["raw_dir"],
        "command_sha256": running.evidence["command_sha256"],
        "release": running.evidence["release"],
        "exit_code": exit_code,
        "terminal_committed": terminal_committed,
        "terminal_lag": terminal_lag,
        "log_sha256": log_sha256,
    }


def build_delivery_receipt(record: "AcquisitionRecord", data_root: Path) -> Tuple[dict[str, Any], dict[str, Any], list]:
    """Audit this acquisition's own confined raw directory; returns (receipt, inventory summary, inventory lines)."""
    identity = _identity(record)
    complete = record.last_entry(AcquisitionState.TRANSFER_COMPLETE)
    verified = record.last_entry(AcquisitionState.TOPIC_VERIFIED)
    if record.state != AcquisitionState.TRANSFER_COMPLETE or complete is None or verified is None:
        raise EvidenceError(f"{record.acquisition_id} is {record.state.value}; delivery evidence needs TRANSFER_COMPLETE")
    transfer = complete.evidence["receipt"]
    raw_rel = record.request.expected_raw_dir(identity["topic"])
    if transfer["raw_dir"] != raw_rel:
        raise EvidenceError("the recorded transfer wrote somewhere other than this acquisition's raw directory")
    raw_dir = confine_tree(Path(data_root).resolve() / raw_rel, storage_base(data_root, "data/raw/data_transfer"))
    if not raw_dir.is_dir():
        raise EvidenceError(f"raw delivery directory {raw_rel} does not exist under the data root")
    lines = build_raw_inventory(raw_dir)
    audit = build_raw_audit(raw_dir)
    parquet = audit["parquet"]
    inventory = summarize_inventory(lines)
    reconciliation = reconcile_delivery(
        expected_topic_messages=verified.evidence["receipt"]["expected_topic_messages"],
        terminal_committed=transfer["terminal_committed"],
        terminal_lag=transfer["terminal_lag"],
        local_readable_rows=parquet["total_readable_rows"],
    )
    if parquet["unreadable_parquet_count"] != 0:
        reconciliation["passed"] = False
    receipt = {
        **identity,
        "kind": "delivery",
        "transfer_attempt_id": transfer["transfer_attempt_id"],
        "raw_dir": raw_rel,
        "file_count": audit["file_count"],
        "parquet_files": parquet["parquet_file_count"],
        "total_bytes": audit["size_summary"]["total_size_bytes"],
        "readable_parquet_files": parquet["readable_parquet_count"],
        "readable_rows": parquet["total_readable_rows"],
        "unreadable_files": parquet["unreadable_parquet_count"],
        "schema_groups": {group["schema_hash"]: {"files": group["file_count"], "rows": group["rows"]} for group in audit["schemas"]["schema_groups"]},
        "inventory_sha256": inventory["inventory_sha256"],
        "inventory_root_sha256": inventory["root_sha256"],
        "inventory_shard_count": len(inventory["shards"]),
        "reconciliation": reconciliation,
    }
    return receipt, inventory, lines
