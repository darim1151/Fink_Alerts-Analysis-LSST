"""Portal adapter contract, form checks and producer-log semantics (layer E).

The supported integration boundary is the public Fink LSST Data Transfer web
form (https://lsst.fink-portal.org/download). Adapters drive that form like a
person would: upload the compiled YAML, read the form back, download the
portal's own normalized configuration, open the final review, and only on an
explicitly armed adapter activate "Submit job" once. Fink's backend
(Livy/Spark, Dash callback endpoints) is never called directly.

Verification uses two independent channels and both must pass:

1. the configuration the portal generates from its form state
   ("Download configuration"), compared semantically with the request;
2. what the form shows: date range, selected packet, Fink filter/block
   buttons, extra SQL, catalogue gauge.

Anything that cannot be observed or parsed counts as a mismatch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Protocol, Tuple

from .portal_config import PortalConfig


PORTAL_URL = "https://lsst.fink-portal.org/download"
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DISPLAY_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y")
_DISPLAY_DATE = re.compile(r"[A-Z][a-z]{2,8}\.? \d{1,2}, \d{4}|\d{1,2} [A-Z][a-z]{2,8} \d{4}")


class PortalError(RuntimeError):
    """Raised when the portal cannot be driven or observed as expected."""


class PortalAutomationUnavailable(PortalError):
    """Raised when browser automation is not installed on this machine."""


class SubmissionDisabledError(PortalError):
    """Raised when submission is attempted on an adapter that was not armed for it."""


@dataclass(frozen=True)
class PortalFormObservation:
    date_value_text: str
    date_display_text: str
    content_values: Tuple[str, ...]
    selected_filter_buttons: Tuple[str, ...]
    selected_block_buttons: Tuple[str, ...]
    filter_buttons_seen: int
    block_buttons_seen: int
    extra_cond_text: str
    catalog_label: str
    alert_estimate_text: str = ""

    def to_evidence(self) -> dict:
        return {
            "date_value_text": self.date_value_text,
            "date_display_text": self.date_display_text,
            "content_values": list(self.content_values),
            "selected_filter_buttons": list(self.selected_filter_buttons),
            "selected_block_buttons": list(self.selected_block_buttons),
            "filter_buttons_seen": self.filter_buttons_seen,
            "block_buttons_seen": self.block_buttons_seen,
            "extra_cond_empty": not self.extra_cond_text.strip(),
            "catalog_label": self.catalog_label,
            "alert_estimate_text": self.alert_estimate_text,
        }


@dataclass(frozen=True)
class FinalReviewObservation:
    reached: bool
    submit_visible: bool
    submit_enabled: bool
    download_visible: bool

    def to_evidence(self) -> dict:
        return {"reached": self.reached, "submit_visible": self.submit_visible, "submit_enabled": self.submit_enabled, "download_visible": self.download_visible}


@dataclass(frozen=True)
class SubmitObservation:
    clicked: bool
    batch_id: Optional[str]
    topic: Optional[str]
    notifications: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ProducerLogObservation:
    available: bool
    text: str


@dataclass(frozen=True)
class ProducerStatus:
    state: str  # not_started | running | complete | failed
    evidence_lines: Tuple[str, ...] = field(default_factory=tuple)
    data_available_marker: bool = False
    end_marker: bool = False


class PortalAdapter(Protocol):
    session_id: str
    live_submit_enabled: bool

    def open(self) -> None: ...

    def upload_config(self, path: Path) -> None: ...

    def observe_form(self) -> PortalFormObservation: ...

    def reach_final_review(self) -> FinalReviewObservation: ...

    def download_config(self, destination_dir: Path) -> Path: ...

    def submit(self) -> SubmitObservation: ...

    def read_producer_log(self) -> ProducerLogObservation: ...

    def close(self) -> None: ...


def observed_dates(observation: PortalFormObservation) -> Tuple[str, ...]:
    """Dates shown by the form: ISO values first, then the human-readable display."""
    found = _ISO_DATE.findall(observation.date_value_text)
    if found:
        return tuple(found)
    parsed = []
    for text in _DISPLAY_DATE.findall(observation.date_display_text):
        cleaned = text.replace(".", "")
        for fmt in _DISPLAY_FORMATS:
            try:
                parsed.append(datetime.strptime(cleaned, fmt).date().isoformat())
                break
            except ValueError:
                continue
    return tuple(parsed)


def check_form_observation(observation: PortalFormObservation, expected: PortalConfig) -> list[str]:
    """Return mismatches between the visible form and the expected configuration."""
    mismatches = []
    dates = observed_dates(observation)
    wanted = (expected.startdate, expected.stopdate)
    if dates != wanted and not (expected.startdate == expected.stopdate and dates == (expected.startdate,)):
        mismatches.append(f"form dates: expected {wanted[0]} to {wanted[1]} inclusive, form shows {list(dates) or 'nothing readable'}")
    if list(observation.content_values) != list(expected.content):
        mismatches.append(f"form content: expected {list(expected.content)}, form shows {list(observation.content_values)}")
    if observation.filter_buttons_seen <= 0 or observation.block_buttons_seen <= 0:
        mismatches.append("form filters/blocks could not be observed")
    if list(observation.selected_filter_buttons) != list(expected.filters):
        mismatches.append(f"form filters: expected {list(expected.filters)}, form has {list(observation.selected_filter_buttons)} selected")
    if list(observation.selected_block_buttons) != list(expected.blocks):
        mismatches.append(f"form blocks: expected {list(expected.blocks)}, form has {list(observation.selected_block_buttons)} selected")
    if (observation.extra_cond_text.strip() or None) != expected.extra_cond:
        mismatches.append("form extra SQL: expected none, the custom filtering box is not empty")
    if expected.catalog_filename is None and "no catalog" not in observation.catalog_label.lower():
        mismatches.append(f"form catalogue: expected none, gauge shows {observation.catalog_label!r}")
    return mismatches


def check_final_review(observation: FinalReviewObservation) -> list[str]:
    mismatches = []
    if not observation.reached:
        mismatches.append("final review step was not reached")
    if not observation.submit_visible:
        mismatches.append("Submit control not visible on the final review")
    if not observation.download_visible:
        mismatches.append("Download configuration control not visible on the final review")
    return mismatches


def classify_producer_log(text: str, topic: str) -> ProducerStatus:
    """Classify the portal job log for `topic` without over-claiming completion.

    The Fink transfer job logs "Starting to send data to topic <t>" before
    writing to Kafka, then "Data available at topic: <t>" and "End." once it
    has finished. Log rows may carry a timestamp/logger prefix, so markers are
    matched at the end of a row. "Starting to send" means running, not done;
    completion needs both terminal markers, in order, for this topic. The
    evidence is the canonical marker text that was found, never raw log rows,
    so no endpoint or account detail can be carried into the registry.
    """
    rows = [row.strip() for row in (text or "").splitlines() if row.strip()]
    data_marker = re.compile(r"Data available at topic:\s*(\S+)\s*$")
    data_index = next((index for index, row in enumerate(rows) if (match := data_marker.search(row)) and match.group(1) == topic), None)
    data_available = data_index is not None
    end_marker = data_available and any(re.search(r"(?:^|\s)End\.$", row) for row in rows[data_index + 1 :])
    running = any(re.search(r"Starting to send data to topic\s+" + re.escape(topic) + r"\s*$", row) for row in rows)
    failed = any(re.search(r"\b(?:traceback|error|exception|failed)\b", row, re.IGNORECASE) for row in rows)
    evidence = tuple(
        marker
        for marker, present in (
            (f"Starting to send data to topic {topic}", running),
            (f"Data available at topic: {topic}", data_available),
            ("End.", end_marker),
        )
        if present
    )
    if data_available and end_marker and not failed:
        return ProducerStatus("complete", evidence, True, True)
    if failed:
        return ProducerStatus("failed", evidence, data_available, end_marker)
    if running or data_available:
        return ProducerStatus("running", evidence, data_available, end_marker)
    return ProducerStatus("not_started", evidence)
