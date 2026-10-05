"""Kafka topic expectation and Arnor transfer handoff (layer F).

Nothing here consumes Kafka, runs `finkctl`, or touches Arnor. It composes the
accepted G2B/G3A primitives into a plan an executor can review and run:

    producer complete
    -> topic metadata pre-check (partition watermarks, no consume/commit)
    -> expected_topic_messages = sum(high - low)
    -> finkctl transfer from <root>/manifests/<topic> (schema dumps land in cwd)
    -> reconciliation (see evidence.py)

`expected_topic_messages` is a transport-level count of what the fresh topic
holds. It is not Rubin scientific completeness.

The run manifest, topic-registry entry, raw path, `finkctl` argv and log path
all come from `run_manifest`, `scopes` and `topic_registry`, so path
confinement, `-nconsumers` validation and the absolute `-outdir` rule are the
accepted ones, not copies.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence

import yaml

from fink_lsst.bulk_transfer.run_manifest import (
    SCHEMA_VERSION as RUN_MANIFEST_SCHEMA_VERSION,
    RunManifest,
    derive_claim_state,
    derive_default_paths,
    manifest_to_dict,
    topic_entry_from_manifest,
    validate_run_manifest,
)
from fink_lsst.bulk_transfer.topic_registry import DEFAULT_TRANSFER_CONSUMERS, build_download_command, build_topic_entry, transfer_log_path
from fink_lsst.data_root import confine_tree, storage_base, validate_path_component

from .receipts import EvidenceError, PartitionWatermarks, TopicWatermarkObservation
from .receipts import expected_topic_messages as _expected_topic_messages
from .states import AcquisitionState

if TYPE_CHECKING:  # pragma: no cover
    from .registry import AcquisitionRecord


class HandoffError(ValueError):
    """Raised when a handoff artifact cannot be built truthfully."""


def expected_topic_messages(partitions: Sequence[PartitionWatermarks]) -> int:
    """Return sum(high - low) over all partitions after sanity checks."""
    try:
        return _expected_topic_messages(partitions)
    except EvidenceError as exc:
        raise HandoffError(str(exc)) from exc


def observe_topic_watermarks(consumer: Any, topic: str, *, timeout: float = 10.0, topic_partition_factory: Optional[Callable[[str, int], Any]] = None) -> TopicWatermarkObservation:
    """Query the watermarks of `topic` and return them bound to the topic that was queried."""
    partitions = watermarks_from_consumer(consumer, topic, timeout=timeout, topic_partition_factory=topic_partition_factory)
    return TopicWatermarkObservation(topic=topic, checked_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), partitions=tuple(partitions))


def watermarks_from_consumer(consumer: Any, topic: str, *, timeout: float = 10.0, topic_partition_factory: Optional[Callable[[str, int], Any]] = None) -> list[PartitionWatermarks]:
    """Read partition watermarks with an already configured confluent-kafka consumer.

    Uses only `list_topics` and `get_watermark_offsets(cached=False)`: no
    subscription, poll, consume or commit. The caller builds the consumer, so
    no credential handling happens here.
    """
    validate_path_component(topic, "topic")
    if topic_partition_factory is None:
        from confluent_kafka import TopicPartition as topic_partition_factory  # noqa: N813 - imported only on the executor host
    metadata = consumer.list_topics(topic, timeout=timeout)
    topic_meta = metadata.topics.get(topic)
    if topic_meta is None or getattr(topic_meta, "error", None) is not None:
        raise HandoffError(f"topic {topic} is not available on the configured cluster")
    partitions = []
    for partition in sorted(topic_meta.partitions):
        low, high = consumer.get_watermark_offsets(topic_partition_factory(topic, partition), timeout=timeout, cached=False)
        partitions.append(PartitionWatermarks(int(partition), int(low), int(high)))
    return partitions


def _require_topic(record: AcquisitionRecord) -> str:
    topic = record.topic
    if not topic:
        raise HandoffError(f"{record.acquisition_id} has no identified topic (state {record.state.value})")
    return topic


def build_run_manifest_for_acquisition(record: AcquisitionRecord) -> RunManifest:
    """Build the adaptive run manifest for an acquisition's identified topic."""
    topic = _require_topic(record)
    request = record.request
    profile = request.profile
    manifest = RunManifest(
        schema_version=RUN_MANIFEST_SCHEMA_VERSION,
        run_name=record.acquisition_id,
        run_id=topic,
        survey=request.survey,
        broker="fink",
        topic=topic,
        batch_id=record.batch_id,
        startdate=request.start,
        stopdate=request.stop,
        date_mode="utc_window",
        scope="single_night" if request.scope == "full_night" else request.scope,
        packet_type="light_static",
        content=profile.packet,
        filters=list(profile.filters),
        is_all_alert=not profile.filters and not profile.blocks and profile.extra_cond is None and profile.catalog_filename is None,
        expected_nights=list(request.expected_dates),
        lifecycle_state="topic_registered",
        notes=(
            f"Acquisition {record.acquisition_id} (fingerprint {record.fingerprint}). Profile {profile.name}: "
            f"Light static packet, no tags/filters/blocks/catalogue/extra SQL. Repository window [{request.start}, {request.stop}) = "
            f"portal {request.portal_startdate} to {request.portal_stopdate} inclusive. Not yet transferred or validated."
        ),
    )
    manifest.paths = derive_default_paths(manifest)
    manifest.claim_state = derive_claim_state(manifest)
    verified = record.last_entry(AcquisitionState.TOPIC_VERIFIED)
    if verified is not None:
        manifest.download_evidence.expected_total_messages = int(verified.evidence["receipt"]["expected_topic_messages"])
    errors, _warnings = validate_run_manifest(manifest)
    if errors:
        raise HandoffError("generated run manifest is invalid: " + "; ".join(errors))
    return manifest


def render_run_manifest_yaml(record: AcquisitionRecord) -> str:
    """Render the run manifest plus acquisition provenance for `configs/runs/`."""
    payload = manifest_to_dict(build_run_manifest_for_acquisition(record))
    payload["acquisition"] = {
        "acquisition_id": record.acquisition_id,
        "fingerprint": record.fingerprint,
        "science_profile": record.request.science_profile,
        "registry_dir": f"configs/acquisitions/{record.acquisition_id}",
        "portal_dates_inclusive": {"startdate": record.request.portal_startdate, "stopdate": record.request.portal_stopdate},
        "state": record.state.value,
    }
    return yaml.safe_dump(payload, sort_keys=False)


def build_topic_entry_for_acquisition(record: AcquisitionRecord) -> dict[str, Any]:
    """Build the non-secret topic-registry entry (append-only convention) for the topic."""
    topic = _require_topic(record)
    request = record.request
    entry = build_topic_entry(
        scope=request.scope,
        survey=request.survey,
        topic=topic,
        startdate=request.start,
        stopdate=request.stop,
        content=request.profile.packet,
        all_alert=True,
        filters=[],
        notes=f"Acquisition {record.acquisition_id}; portal {request.portal_startdate} to {request.portal_stopdate} inclusive.",
    )
    entry["batch_id"] = record.batch_id
    entry["acquisition_id"] = record.acquisition_id
    entry["lifecycle_state"] = "topic_registered"
    return entry


@dataclass(frozen=True)
class TransferPlan:
    acquisition_id: str
    topic: str
    argv: list
    working_dir: Path
    log_path: Path
    raw_dir: Path
    raw_dir_relative: str
    command_sha256: str
    expected_topic_messages: Optional[int]
    ready_for_transfer: bool
    blocking_reasons: tuple = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "acquisition_id": self.acquisition_id,
            "topic": self.topic,
            "raw_dir_relative": self.raw_dir_relative,
            "command_sha256": self.command_sha256,
            "transfer_command": list(self.argv),
            "working_dir": str(self.working_dir),
            "log_path": str(self.log_path),
            "raw_dir": str(self.raw_dir),
            "expected_topic_messages": self.expected_topic_messages,
            "ready_for_transfer": self.ready_for_transfer,
            "blocking_reasons": list(self.blocking_reasons),
            "executes_nothing": True,
        }


def build_transfer_plan(record: AcquisitionRecord, data_root: Path, nconsumers: int = DEFAULT_TRANSFER_CONSUMERS) -> TransferPlan:
    """Plan (never run) the Arnor transfer for an acquisition's topic."""
    manifest = build_run_manifest_for_acquisition(record)
    topic = manifest.topic
    argv = build_download_command(topic_entry_from_manifest(manifest), data_root, nconsumers)
    manifests_base = storage_base(data_root, "manifests")
    working_dir = confine_tree(manifests_base / validate_path_component(topic, "topic"), manifests_base)
    verified = record.last_entry(AcquisitionState.TOPIC_VERIFIED)
    reasons = []
    if record.state != AcquisitionState.TOPIC_VERIFIED:
        reasons.append(f"state is {record.state.value}; the transfer starts only from TOPIC_VERIFIED")
    if verified is None:
        reasons.append("no Kafka topic metadata pre-check (expected_topic_messages) is recorded")
    return TransferPlan(
        acquisition_id=record.acquisition_id,
        topic=topic,
        argv=argv,
        working_dir=working_dir,
        log_path=transfer_log_path(data_root, topic),
        raw_dir=Path(argv[argv.index("-outdir") + 1]),
        raw_dir_relative=manifest.paths.raw_dir,
        command_sha256=hashlib.sha256(json.dumps(argv, separators=(",", ":")).encode("utf-8")).hexdigest(),
        expected_topic_messages=int(verified.evidence["receipt"]["expected_topic_messages"]) if verified else None,
        ready_for_transfer=not reasons,
        blocking_reasons=tuple(reasons),
    )


def render_transfer_wrapper(plan: TransferPlan, *, python_env_bin: str, release_dir: str) -> str:
    """Render a G3A-style wrapper script for review; it is never executed by this code."""
    if not plan.ready_for_transfer:
        raise HandoffError("transfer plan is not ready: " + "; ".join(plan.blocking_reasons))
    command = " ".join(shlex.quote(token) for token in plan.argv)
    exit_file = plan.working_dir / "transfer_exit.txt"
    return "\n".join(
        [
            "#!/bin/bash",
            f"# Transfer wrapper for acquisition {plan.acquisition_id}; generated for review, run manually in a fink-* tmux session.",
            "# cwd is the topic's manifests directory because finkctl --dump_schemas writes schema files to cwd.",
            f"# expected_topic_messages (transport count, not scientific completeness): {plan.expected_topic_messages}",
            "set -o pipefail",
            "umask 077",
            f"# release: {release_dir}",
            f"export PATH={shlex.quote(python_env_bin)}:$PATH",
            f'mkdir -p "{plan.working_dir}" "{plan.log_path.parent}"',
            f'cd "{plan.working_dir}" || exit 91',
            f'{{ echo "# start_utc=$(date -u +%FT%TZ) cwd=$PWD"; echo "# command: {command}"; }} | tee -a "{plan.log_path}"',
            f'{command} 2>&1 | tee -a "{plan.log_path}"',
            "RC=${PIPESTATUS[0]}",
            f'echo "# end_utc=$(date -u +%FT%TZ) exit_code=$RC" | tee -a "{plan.log_path}"',
            f'echo "$RC $(date -u +%FT%TZ)" > "{exit_file}"',
            "",
        ]
    )

