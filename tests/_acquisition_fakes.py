"""Deterministic local stand-ins for the Fink portal used by acquisition tests.

The fake models the portal's observable contract (upload a YAML, read back the
form, download the portal-normalized configuration, reach the final review,
submit once, read the producer log) without any network access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml

from fink_lsst.acquisition.portal import (
    FinalReviewObservation,
    PortalFormObservation,
    ProducerLogObservation,
    SubmissionDisabledError,
    SubmitObservation,
)


@dataclass
class FakePortal:
    """In-memory portal. Tweak attributes to simulate portal behaviour changes."""

    session_id: str = "fake-session-1"
    live_submit_enabled: bool = False
    # Optional hooks that rewrite the configuration the portal "normalizes" or displays.
    normalize: Callable[[dict], dict] = lambda mapping: mapping
    display: Callable[[dict], dict] = lambda mapping: mapping
    submit_behavior: str = "topic"  # topic | batch_only | lost | raise
    batch_id: str = "41"
    topic: str = "ftransfer_lsst_2026-10-05_123456"
    producer_log: Optional[str] = None
    calls: list = field(default_factory=list)
    submit_clicks: int = 0
    _loaded: Optional[dict] = None

    def open(self) -> None:
        self.calls.append("open")

    def upload_config(self, path: Path) -> None:
        self.calls.append("upload")
        self._loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8"))

    def observe_form(self) -> PortalFormObservation:
        self.calls.append("observe")
        shown = self.display(dict(self._loaded))
        dates = shown["dates"]
        return PortalFormObservation(
            date_value_text=f"{dates['startdate']} – {dates['stopdate']}",
            date_display_text="",
            content_values=tuple(shown["content"]),
            selected_filter_buttons=tuple(shown["filters"]),
            selected_block_buttons=tuple(shown["blocks"]),
            filter_buttons_seen=11,
            block_buttons_seen=16,
            extra_cond_text=shown["extra_cond"] or "",
            catalog_label="No catalog" if not shown["catalog_filename"] else "1 source",
            alert_estimate_text="21,000,000 alerts",
        )

    def download_config(self, destination_dir: Path) -> Path:
        self.calls.append("download")
        normalized = self.normalize(dict(self._loaded))
        path = Path(destination_dir) / "datatransfer_20261005_000000.yml"
        path.write_text(yaml.safe_dump(normalized, sort_keys=True), encoding="utf-8")
        return path

    def reach_final_review(self) -> FinalReviewObservation:
        self.calls.append("final_review")
        return FinalReviewObservation(reached=True, submit_visible=True, submit_enabled=True, download_visible=True)

    def submit(self) -> SubmitObservation:
        self.calls.append("submit")
        if not self.live_submit_enabled:
            raise SubmissionDisabledError("fake portal constructed without live submission")
        self.submit_clicks += 1
        if self.submit_behavior == "raise":
            raise TimeoutError("network response lost after click")
        if self.submit_behavior == "lost":
            return SubmitObservation(clicked=True, batch_id=None, topic=None, notifications=())
        if self.submit_behavior == "batch_only":
            return SubmitObservation(clicked=True, batch_id=self.batch_id, topic=None, notifications=("Job submitted",))
        return SubmitObservation(clicked=True, batch_id=self.batch_id, topic=self.topic, notifications=("Job submitted",))

    def read_producer_log(self) -> ProducerLogObservation:
        self.calls.append("producer_log")
        if self.producer_log is None:
            return ProducerLogObservation(available=False, text="")
        return ProducerLogObservation(available=True, text=self.producer_log)

    def close(self) -> None:
        self.calls.append("close")


@dataclass
class FakeApprover:
    approve_fingerprint: Optional[str] = None
    refuse: bool = False
    calls: int = 0

    def approve(self, record, review_text: str):
        from fink_lsst.acquisition.orchestrator import Approval

        self.calls += 1
        if self.refuse:
            return None
        return Approval(method="interactive_tty", approved_fingerprint=self.approve_fingerprint or record.fingerprint, statement="typed acquisition id")


def write_raw_parquet(raw_dir: Path, rows_per_file) -> Path:
    """Write tiny readable Parquet files standing in for a raw delivery."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True)
    offset = 0
    for index, rows in enumerate(rows_per_file):
        pq.write_table(pa.table({"diaSourceId": list(range(offset, offset + rows))}), raw_dir / f"part-{index}.parquet")
        offset += rows
    return raw_dir


def delivery_summary_for(raw_dir: Path, rows_per_file, *, topic: str, expected: int, committed: int, lag: int = 0) -> dict:
    from fink_lsst.acquisition.evidence import build_delivery_evidence

    summary, _lines = build_delivery_evidence(write_raw_parquet(raw_dir, rows_per_file), topic=topic, expected_topic_messages=expected, terminal_committed=committed, terminal_lag=lag)
    return summary
