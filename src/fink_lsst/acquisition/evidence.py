"""Delivery reconciliation and scalable integrity evidence (layer G).

Reconciliation
--------------
A delivery is validated only when three independent transport counts are
equal and the consumer lag is zero:

    expected_topic_messages (pre-transfer Kafka watermarks)
    = terminal committed offsets reported by finkctl
    = locally readable Parquet rows

There is no tolerance: the historical week's 0.168% gap fails. Passing proves
the delivery matches the topic, nothing about Rubin-level completeness.

Integrity evidence
------------------
G3A committed a full per-file SHA-256 inventory (7,986 lines). A month can
hold hundreds of thousands of small files, so for orchestrated acquisitions:

- the full inventory (`<sha256>  <relative path>`, sorted by path, the G3A
  format) stays on the data plane next to the raw files, which stay
  immutable;
- Git receives only small evidence: counts, bytes, readable rows, schema
  groups, the inventory's SHA-256, 256 shard digests keyed by
  sha256(relative_path)[:2] (so a changed file can be localized without the
  full list) and a root digest over the shards.

The committed G3A inventory is left exactly as it is.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Sequence, Tuple

from fink_lsst.bulk_transfer.raw_audit import build_raw_audit
from fink_lsst.data_root import PathConfinementError


GIT_EVIDENCE_POLICY = {
    "name": "summary_with_shard_digests_v1",
    "full_inventory_location": "data plane: <root>/manifests/<topic>/raw_sha256_inventory.txt",
    "git_contents": ["counts", "bytes", "readable rows", "schema groups", "inventory sha256", "shard digests", "root digest", "reconciliation"],
    "applies_to": "orchestrated acquisitions (G3B.0 onward); the committed G3A inventory is unchanged",
}
SHARD_PREFIX_HEX = 2
_CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def build_raw_inventory(raw_dir: Path) -> list[str]:
    """Return `<sha256>  <relative path>` lines for every regular file, sorted by path.

    Symlinks and special files are refused rather than followed.
    """
    root = Path(raw_dir)
    entries = []
    for current, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(current) / name
            if path.is_symlink():
                raise PathConfinementError(f"raw delivery contains a symlink: {path}")
            if name in filenames and not path.is_file():
                raise PathConfinementError(f"raw delivery contains a non-regular file: {path}")
        for name in filenames:
            path = Path(current) / name
            entries.append((path.relative_to(root).as_posix(), path))
    return [f"{sha256_file(path)}  {relative}" for relative, path in sorted(entries)]


def inventory_text(lines: Sequence[str]) -> str:
    return "".join(f"{line}\n" for line in lines)


def inventory_sha256(lines: Sequence[str]) -> str:
    return hashlib.sha256(inventory_text(lines).encode("utf-8")).hexdigest()


def summarize_inventory(lines: Sequence[str]) -> dict[str, Any]:
    """Small, committable summary of a per-file inventory."""
    shards: dict[str, list[str]] = {}
    for line in lines:
        _digest, relative = _split(line)
        shards.setdefault(hashlib.sha256(relative.encode("utf-8")).hexdigest()[:SHARD_PREFIX_HEX], []).append(line)
    shard_summary = {
        key: {"files": len(items), "sha256": inventory_sha256(sorted(items, key=lambda item: _split(item)[1]))}
        for key, items in sorted(shards.items())
    }
    root_text = "".join(f"{key} {value['files']} {value['sha256']}\n" for key, value in shard_summary.items())
    return {
        "file_count": len(lines),
        "inventory_sha256": inventory_sha256(lines),
        "inventory_format": "<sha256>  <relative path>, sorted by path, newline-terminated",
        "shard_scheme": f"sha256(relative_path)[:{SHARD_PREFIX_HEX}]",
        "shards": shard_summary,
        "root_sha256": hashlib.sha256(root_text.encode("utf-8")).hexdigest(),
    }


def reconcile_delivery(*, expected_topic_messages: int, terminal_committed: int, terminal_lag: int, local_readable_rows: int) -> dict[str, Any]:
    """Exact three-way transport reconciliation; no tolerance."""
    counts = (expected_topic_messages, terminal_committed, local_readable_rows)
    passed = all(isinstance(value, int) and not isinstance(value, bool) for value in counts) and counts[0] > 0 and len(set(counts)) == 1 and terminal_lag == 0
    return {
        "passed": passed,
        "expected_topic_messages": expected_topic_messages,
        "terminal_committed": terminal_committed,
        "terminal_lag": terminal_lag,
        "local_readable_rows": local_readable_rows,
        "delta_expected_minus_local": expected_topic_messages - local_readable_rows,
        "delta_expected_minus_committed": expected_topic_messages - terminal_committed,
        "meaning": "transport reconciliation of the delivered topic; not Rubin scientific completeness",
    }


def build_delivery_evidence(raw_dir: Path, *, topic: str, expected_topic_messages: int, terminal_committed: int, terminal_lag: int) -> Tuple[dict[str, Any], list[str]]:
    """Return (small committable evidence, full inventory lines for the data plane)."""
    lines = build_raw_inventory(raw_dir)
    audit = build_raw_audit(raw_dir)
    parquet = audit["parquet"]
    summary = {
        "topic": topic,
        "raw_dir": str(raw_dir),
        "raw_file_count": audit["file_count"],
        "parquet_files": parquet["parquet_file_count"],
        "raw_bytes": audit["size_summary"]["total_size_bytes"],
        "readable_parquet_files": parquet["readable_parquet_count"],
        "unreadable_files": parquet["unreadable_parquet_count"],
        "readable_rows": parquet["total_readable_rows"],
        "schema_group_count": audit["schemas"]["schema_group_count"],
        "schema_groups": {group["schema_hash"]: {"files": group["file_count"], "rows": group["rows"]} for group in audit["schemas"]["schema_groups"]},
        "inventory": summarize_inventory(lines),
        "reconciliation": reconcile_delivery(
            expected_topic_messages=expected_topic_messages,
            terminal_committed=terminal_committed,
            terminal_lag=terminal_lag,
            local_readable_rows=parquet["total_readable_rows"],
        ),
        "git_evidence_policy": GIT_EVIDENCE_POLICY["name"],
    }
    return summary, lines


def _split(line: str) -> Tuple[str, str]:
    digest, separator, relative = line.partition("  ")
    if not separator or len(digest) != 64 or not relative:
        raise ValueError(f"malformed inventory line: {line!r}")
    return digest, relative
