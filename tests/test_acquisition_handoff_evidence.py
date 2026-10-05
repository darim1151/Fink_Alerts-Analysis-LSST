"""Kafka/Arnor handoff, transport expectation and scalable evidence tests (FINK-G3B.0)."""

import hashlib
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from _acquisition_fakes import FakeApprover, FakePortal
from fink_lsst.acquisition.evidence import (
    GIT_EVIDENCE_POLICY,
    build_delivery_evidence,
    build_raw_inventory,
    inventory_sha256,
    reconcile_delivery,
    summarize_inventory,
)
from fink_lsst.acquisition.handoff import (
    HandoffError,
    PartitionWatermarks,
    build_run_manifest_for_acquisition,
    build_topic_entry_for_acquisition,
    build_transfer_plan,
    expected_topic_messages,
    render_run_manifest_yaml,
    render_transfer_wrapper,
    watermarks_from_consumer,
)
from fink_lsst.acquisition.orchestrator import AcquisitionOrchestrator
from fink_lsst.acquisition.registry import AcquisitionRegistry
from fink_lsst.bulk_transfer.run_manifest import manifest_from_dict, manifest_to_dict, validate_run_manifest
from fink_lsst.bulk_transfer.topic_registry import build_download_command
from fink_lsst.data_root import PathConfinementError


AS_OF = date(2026, 10, 5)
TOPIC = "ftransfer_lsst_2026-10-05_123456"
G3A_INVENTORY = Path("configs/delivery_evidence/ftransfer_lsst_2026-10-04_177446/raw_sha256_inventory.txt")


class Clock:
    def __init__(self):
        self.tick = 0

    def __call__(self):
        self.tick += 1
        return f"2026-10-05T01:{self.tick // 60:02d}:{self.tick % 60:02d}Z"


def _identified(tmp_path, start="2026-02-25", stop="2026-03-25"):
    registry = AcquisitionRegistry(tmp_path / "acquisitions", clock=Clock())
    orchestrator = AcquisitionOrchestrator(registry, topic_registry_path=Path("configs/data_transfer_topics.yaml"), clock=Clock(), workdir=tmp_path / "work")
    record = orchestrator.record(orchestrator.plan(start, stop, as_of=AS_OF))
    portal = FakePortal(live_submit_enabled=True)
    record = orchestrator.verify_portal(record, portal)
    record = orchestrator.submit(record, portal, FakeApprover())
    return orchestrator, record


def _data_root(tmp_path):
    root = tmp_path / "FINK"
    for relative in ("data/raw/data_transfer", "data/processed/data_transfer", "outputs/data_transfer", "manifests", "logs"):
        (root / relative).mkdir(parents=True)
    return root


# ---------------------------------------------------------------- transport expectation


def test_expected_topic_messages_is_sum_of_high_minus_low():
    g3a_highs = [80161, 80174, 79745, 79628, 79761, 79103, 79901, 79995, 79909, 79670]
    partitions = [PartitionWatermarks(index, 0, high) for index, high in enumerate(g3a_highs)]
    assert expected_topic_messages(partitions) == 798047
    assert expected_topic_messages([PartitionWatermarks(0, 5, 15), PartitionWatermarks(1, 100, 100)]) == 10


@pytest.mark.parametrize(
    "partitions",
    [
        [],
        [PartitionWatermarks(0, 10, 5)],
        [PartitionWatermarks(0, -1, 5)],
        [PartitionWatermarks(0, 0, 5), PartitionWatermarks(0, 0, 5)],
    ],
)
def test_invalid_watermarks_are_rejected(partitions):
    with pytest.raises(HandoffError):
        expected_topic_messages(partitions)


def test_watermarks_from_consumer_reads_metadata_without_consuming():
    class FakeTopicPartition:
        def __init__(self, topic, partition):
            self.topic, self.partition = topic, partition

    class FakeConsumer:
        def __init__(self):
            self.calls = []

        def list_topics(self, topic, timeout):
            self.calls.append(("list_topics", topic))

            class Meta:
                topics = {topic: type("T", (), {"partitions": {0: None, 1: None}, "error": None})()}

            return Meta()

        def get_watermark_offsets(self, tp, timeout, cached):
            self.calls.append(("watermarks", tp.partition, cached))
            return (0, 7) if tp.partition == 0 else (2, 5)

        def consume(self, *args, **kwargs):
            raise AssertionError("must not consume")

        def poll(self, *args, **kwargs):
            raise AssertionError("must not poll")

        def commit(self, *args, **kwargs):
            raise AssertionError("must not commit")

    consumer = FakeConsumer()
    partitions = watermarks_from_consumer(consumer, TOPIC, topic_partition_factory=FakeTopicPartition)
    assert [(p.partition, p.low, p.high) for p in partitions] == [(0, 0, 7), (1, 2, 5)]
    assert expected_topic_messages(partitions) == 10
    assert all(call[2] is False for call in consumer.calls if call[0] == "watermarks")


# ---------------------------------------------------------------- run manifest / transfer plan


def test_run_manifest_for_date_range_acquisition_is_valid_and_truthful(tmp_path):
    _orchestrator, record = _identified(tmp_path)
    manifest = build_run_manifest_for_acquisition(record)
    assert manifest.scope == "date_range"
    assert manifest.topic == TOPIC
    assert manifest.batch_id == "41"
    assert manifest.startdate == "2026-02-25" and manifest.stopdate == "2026-03-25"
    assert len(manifest.expected_nights) == 28
    assert manifest.paths.raw_dir == f"data/raw/data_transfer/date_range/2026-02-25_to_2026-03-25/{TOPIC}"
    assert manifest.packet_type == "light_static" and manifest.content == "Light static packet"
    assert manifest.filters == [] and manifest.is_all_alert is True
    assert manifest.lifecycle_state == "topic_registered"
    assert manifest.claim_state.week_completeness == "blocked"  # a date range is not a week
    assert manifest.claim_state.range_completeness == "unresolved"
    errors, _warnings = validate_run_manifest(manifest)
    assert errors == []
    reloaded = manifest_from_dict(manifest_to_dict(manifest))
    assert reloaded.scope == "date_range"
    import yaml as _yaml

    rendered = _yaml.safe_load(render_run_manifest_yaml(record))
    assert rendered["acquisition"]["fingerprint"] == record.fingerprint
    assert rendered["acquisition"]["portal_dates_inclusive"] == {"startdate": "2026-02-25", "stopdate": "2026-03-24"}
    assert validate_run_manifest(manifest_from_dict(rendered))[0] == []


def test_single_night_acquisition_uses_full_night_scope_like_g3a(tmp_path):
    _orchestrator, record = _identified(tmp_path, "2026-02-26", "2026-02-27")
    manifest = build_run_manifest_for_acquisition(record)
    assert manifest.scope == "single_night"
    assert manifest.paths.raw_dir == f"data/raw/data_transfer/full_night/2026-02-26_to_2026-02-27/{TOPIC}"
    entry = build_topic_entry_for_acquisition(record)
    assert entry["scope"] == "full_night"


def test_topic_entry_for_date_range_uses_range_claim_policy(tmp_path):
    _orchestrator, record = _identified(tmp_path)
    entry = build_topic_entry_for_acquisition(record)
    assert entry["scope"] == "date_range"
    assert entry["claim_policy"] == {"range_complete_default": "unresolved", "allow_completeness_only_after_validation": True}
    assert entry["raw_delivery_dir"] == f"data/raw/data_transfer/date_range/2026-02-25_to_2026-03-25/{TOPIC}"
    assert entry["acquisition_id"] == record.acquisition_id


def test_transfer_plan_reuses_accepted_command_generation(tmp_path):
    _orchestrator, record = _identified(tmp_path)
    root = _data_root(tmp_path)
    plan = build_transfer_plan(record, root)
    manifest = build_run_manifest_for_acquisition(record)
    from fink_lsst.bulk_transfer.run_manifest import topic_entry_from_manifest

    assert plan.argv == build_download_command(topic_entry_from_manifest(manifest), root, 4)
    assert plan.argv == [
        "finkctl", "transfer", "-survey", "lsst", "-topic", TOPIC,
        "-outdir", str(root.resolve() / f"data/raw/data_transfer/date_range/2026-02-25_to_2026-03-25/{TOPIC}"),
        "-nconsumers", "4", "--dump_schemas", "--verbose",
    ]
    assert "-limit" not in plan.argv and not any("restart" in token for token in plan.argv)
    assert plan.working_dir == root.resolve() / "manifests" / TOPIC
    assert plan.log_path == root.resolve() / "logs" / f"{TOPIC}.transfer.log"
    assert plan.ready_for_transfer is False  # topic not yet verified against Kafka metadata
    assert plan.blocking_reasons


def test_transfer_plan_is_ready_only_after_topic_verification(tmp_path):
    orchestrator, record = _identified(tmp_path)
    record = orchestrator.observe_producer(record, FakePortal(producer_log=f"Data available at topic: {TOPIC}\nEnd.\n"))
    record = orchestrator.record_topic_metadata(record, [PartitionWatermarks(0, 0, 10)], checked_utc="2026-10-05T01:00:00Z")
    plan = build_transfer_plan(record, _data_root(tmp_path))
    assert plan.ready_for_transfer is True
    assert plan.expected_topic_messages == 10
    wrapper = render_transfer_wrapper(plan, python_env_bin="/opt/env/bin", release_dir="/opt/releases/abc123")
    assert 'cd "' + str(plan.working_dir) + '"' in wrapper
    assert "--dump_schemas" in wrapper and "-nconsumers 4" in wrapper
    assert "set -o pipefail" in wrapper and "umask 077" in wrapper
    assert "auth show" not in wrapper and "restart" not in wrapper


def test_transfer_plan_keeps_path_confinement(tmp_path):
    _orchestrator, record = _identified(tmp_path)
    root = _data_root(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "manifests" / TOPIC).symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathConfinementError):
        build_transfer_plan(record, root)


def test_handoff_requires_an_identified_topic(tmp_path):
    registry = AcquisitionRegistry(tmp_path / "acquisitions", clock=Clock())
    orchestrator = AcquisitionOrchestrator(registry, topic_registry_path=Path("configs/data_transfer_topics.yaml"), clock=Clock(), workdir=tmp_path / "work")
    record = orchestrator.record(orchestrator.plan("2026-02-25", "2026-03-25", as_of=AS_OF))
    with pytest.raises(HandoffError):
        build_run_manifest_for_acquisition(record)
    with pytest.raises(HandoffError):
        build_transfer_plan(record, _data_root(tmp_path))


# ---------------------------------------------------------------- scalable integrity evidence


def test_inventory_format_reproduces_the_committed_g3a_inventory_digest():
    lines = G3A_INVENTORY.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 7986
    assert inventory_sha256(lines) == "6dcc395bffb3eeff95163c7fbe13060b6983498393f1d02c963258d0c060a6db"
    summary = summarize_inventory(lines)
    assert summary["file_count"] == 7986
    assert summary["inventory_sha256"] == "6dcc395bffb3eeff95163c7fbe13060b6983498393f1d02c963258d0c060a6db"
    assert summary["shard_scheme"] == "sha256(relative_path)[:2]"
    assert sum(item["files"] for item in summary["shards"].values()) == 7986
    assert len(summary["shards"]) <= 256
    assert len(summary["root_sha256"]) == 64


def test_shard_digests_localize_a_changed_file():
    lines = G3A_INVENTORY.read_text(encoding="utf-8").splitlines()
    changed = list(lines)
    digest, name = changed[100].split("  ", 1)
    changed[100] = ("0" * 64) + "  " + name
    before, after = summarize_inventory(lines), summarize_inventory(changed)
    differing = [key for key in before["shards"] if before["shards"][key] != after["shards"][key]]
    assert len(differing) == 1
    assert differing[0] == hashlib.sha256(name.encode("utf-8")).hexdigest()[:2]
    assert before["root_sha256"] != after["root_sha256"]


def _write_raw(raw_dir, rows_per_file):
    raw_dir.mkdir(parents=True)
    offset = 0
    for index, rows in enumerate(rows_per_file):
        table = pa.table({"diaSourceId": list(range(offset, offset + rows)), "midpointMjdTai": [61096.1] * rows})
        pq.write_table(table, raw_dir / f"part-{index}.parquet")
        offset += rows


def test_raw_inventory_and_delivery_evidence(tmp_path):
    raw = tmp_path / "raw" / TOPIC
    _write_raw(raw, [3, 4, 3])
    lines = build_raw_inventory(raw)
    assert [line.split("  ", 1)[1] for line in lines] == ["part-0.parquet", "part-1.parquet", "part-2.parquet"]
    assert lines[0].split("  ", 1)[0] == hashlib.sha256((raw / "part-0.parquet").read_bytes()).hexdigest()
    summary, inventory_lines = build_delivery_evidence(raw, topic=TOPIC, expected_topic_messages=10, terminal_committed=10, terminal_lag=0)
    assert inventory_lines == lines
    assert summary["raw_file_count"] == 3
    assert summary["readable_rows"] == 10
    assert summary["unreadable_files"] == 0
    assert summary["schema_group_count"] == 1
    assert summary["reconciliation"]["passed"] is True
    assert summary["git_evidence_policy"] == GIT_EVIDENCE_POLICY["name"]
    assert "lines" not in summary["inventory"]  # small evidence carries digests, not per-file lines
    assert summary["inventory"]["file_count"] == 3


def test_raw_inventory_rejects_symlinks(tmp_path):
    raw = tmp_path / "raw" / TOPIC
    _write_raw(raw, [1])
    (raw / "link.parquet").symlink_to(raw / "part-0.parquet")
    with pytest.raises(PathConfinementError):
        build_raw_inventory(raw)


@pytest.mark.parametrize(
    "expected, committed, lag, local, passed",
    [
        (798047, 798047, 0, 798047, True),
        (1636189, 1636189, 0, 1633438, False),  # historical week gap must never pass
        (10, 9, 1, 9, False),
        (10, 10, 0, 11, False),
        (0, 0, 0, 0, False),
    ],
)
def test_three_way_reconciliation_has_no_tolerance(expected, committed, lag, local, passed):
    result = reconcile_delivery(expected_topic_messages=expected, terminal_committed=committed, terminal_lag=lag, local_readable_rows=local)
    assert result["passed"] is passed
    assert result["expected_topic_messages"] == expected
    assert "completeness" not in " ".join(result)
