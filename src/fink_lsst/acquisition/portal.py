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

Submission boundary (FINK-G3B.0-R2). A live submit needs a one-use
`SubmitAuthorization` issued by the orchestrator only after portal
verification, human approval, an immediate re-observation of the form, and a
durable claim in the external submission authority. The authorization is
bound to one browser-context generation and to the expected scientific
state. Adapters admit at most one outgoing Dash callback for "Submit job",
recognised structurally (JSON parsed, so escaped ids are still recognised),
carrying `n_clicks >= 1` and form state equal to the canonical request.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Protocol, Tuple
from urllib.parse import urlsplit

from .portal_config import PortalConfig


PORTAL_URL = "https://lsst.fink-portal.org/download"
SUBMIT_COMPONENT = "submit_datatransfer"
DASH_CALLBACK_PATH_SUFFIX = "/_dash-update-component"
# Form state the portal's submit callback sends (public source:
# astrolabsoftware/lsst.fink-portal.org apps/datatransfer.py, `submit_job`).
SUBMIT_CALLBACK_STATE_KEYS = (
    "date-range-picker.value",
    "tag_select.data",
    "blocks_select.data",
    "field_select.value",
    "extra_cond.value",
    "object-catalog.data",
    "upload-data.filename",
)
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
    callback_admitted: Optional[bool] = None
    guard_reason: str = ""


@dataclass(frozen=True)
class PortalScientificState:
    """Everything on the form that changes what Fink delivers, in canonical form."""

    startdate: str
    stopdate: str
    content: Tuple[str, ...]
    filters: Tuple[str, ...]
    blocks: Tuple[str, ...]
    extra_cond: Optional[str]
    catalog_none: bool

    def to_dict(self) -> dict:
        return {
            "startdate": self.startdate,
            "stopdate": self.stopdate,
            "content": list(self.content),
            "filters": sorted(self.filters),
            "blocks": sorted(self.blocks),
            "extra_cond": self.extra_cond,
            "catalog_none": self.catalog_none,
        }

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def normalize_extra_cond(text: Optional[str]) -> Optional[str]:
    """No condition at all, as the transfer job sees it: None, blank, or only empty `;`-separated parts."""
    if text is None:
        return None
    parts = [part.strip() for part in str(text).split(";")]
    return str(text).strip() if any(parts) else None


def scientific_state_from_config(config: PortalConfig) -> PortalScientificState:
    return PortalScientificState(
        startdate=config.startdate,
        stopdate=config.stopdate,
        content=tuple(config.content),
        filters=tuple(config.filters),
        blocks=tuple(config.blocks),
        extra_cond=normalize_extra_cond(config.extra_cond),
        catalog_none=config.catalog_filename is None,
    )


@dataclass(frozen=True)
class SubmitAuthorization:
    """One-use permission to activate Submit once, bound to request, context and approved state."""

    attempt_id: str
    fingerprint: str
    acquisition_id: str
    request_sha256: str
    portal_config_sha256: str
    context_id: Optional[str]
    expected_config: Optional[PortalConfig]
    approved_state_digest: str
    nonce: str


_ISSUED: set = set()
_ISSUED_LOCK = threading.Lock()


def issue_submit_authorization(
    *,
    authority: Any,
    attempt_id: str,
    fingerprint: str,
    acquisition_id: str,
    request_sha256: str,
    portal_config_sha256: str,
    context_id: str,
    expected_config: PortalConfig,
    approved_state_digest: str,
) -> SubmitAuthorization:
    """Issue a one-use authorization; refused unless `authority` holds this exact claim, still unused.

    Only the orchestrator calls this, after verification, approval, the
    immediate re-observation and the durable claim.
    """
    if not context_id or expected_config is None:
        raise SubmissionDisabledError("an authorization needs a live browser context and the expected configuration")
    attempt = authority.get(fingerprint) if authority is not None else None
    if attempt is None or attempt.attempt_id != attempt_id or attempt.status != "claimed" or attempt.browser_context_id != context_id:
        raise SubmissionDisabledError("no matching, unused submission-authority claim exists for this authorization")
    if attempt.acquisition_id != acquisition_id or attempt.request_sha256 != request_sha256 or attempt.portal_config_sha256 != portal_config_sha256:
        raise SubmissionDisabledError("the submission-authority claim is bound to another request")
    if scientific_state_from_config(expected_config).digest() != approved_state_digest:
        raise SubmissionDisabledError("approved state does not equal the canonical request")
    authorization = SubmitAuthorization(
        attempt_id=attempt_id,
        fingerprint=fingerprint,
        acquisition_id=acquisition_id,
        request_sha256=request_sha256,
        portal_config_sha256=portal_config_sha256,
        context_id=context_id,
        expected_config=expected_config,
        approved_state_digest=approved_state_digest,
        nonce=secrets.token_hex(16),
    )
    with _ISSUED_LOCK:
        _ISSUED.add(authorization.nonce)
    return authorization


def consume_submit_authorization(authorization: Any) -> SubmitAuthorization:
    """Spend an authorization; anything not issued, or already spent, is refused."""
    if not isinstance(authorization, SubmitAuthorization):
        raise SubmissionDisabledError("a live submit needs a SubmitAuthorization issued by the orchestrator")
    with _ISSUED_LOCK:
        if authorization.nonce not in _ISSUED:
            raise SubmissionDisabledError("submit authorization was not issued by the orchestrator or was already used")
        _ISSUED.discard(authorization.nonce)
    return authorization


@dataclass(frozen=True)
class DashRequestDecision:
    kind: str  # unrelated | submit_mount | submit_click | malformed
    clicks: Optional[int] = None
    states: Mapping[str, Any] = field(default_factory=dict)
    reason: str = ""


def classify_dash_request(url: str, method: str, body: Optional[str]) -> DashRequestDecision:
    """Structurally classify an outgoing request with respect to the Submit control.

    The body is parsed as JSON before anything is decided, so escaped
    component ids are recognised. A Dash callback body that cannot be parsed,
    or that references the Submit control in an unexpected shape, is
    `malformed` (callers block it). Non-POST requests and POSTs to other
    endpoints that do not reference the control cannot run Dash callbacks and
    are `unrelated`.
    """
    if str(method or "").upper() != "POST":
        return DashRequestDecision("unrelated")
    is_callback = urlsplit(str(url or "")).path.endswith(DASH_CALLBACK_PATH_SUFFIX)
    try:
        payload = json.loads(body) if body else None
    except (TypeError, ValueError):
        return DashRequestDecision("malformed", reason="unparseable callback body") if is_callback else DashRequestDecision("unrelated")
    if is_callback and not isinstance(payload, dict):
        return DashRequestDecision("malformed", reason="callback body is not a JSON object")
    if not _references(payload, SUBMIT_COMPONENT):
        return DashRequestDecision("unrelated")
    if not is_callback:
        return DashRequestDecision("malformed", reason="Submit control referenced outside the Dash callback endpoint")
    inputs, state = payload.get("inputs"), payload.get("state", [])
    if not isinstance(inputs, list) or not isinstance(state, list):
        return DashRequestDecision("malformed", reason="callback inputs/state are not lists")
    submit_inputs = [item for item in inputs if isinstance(item, dict) and item.get("id") == SUBMIT_COMPONENT]
    if len(submit_inputs) != 1 or submit_inputs[0].get("property") != "n_clicks" or len(inputs) != 1:
        return DashRequestDecision("malformed", reason="unexpected Submit callback inputs")
    if any(isinstance(item, dict) and item.get("id") == SUBMIT_COMPONENT for item in state):
        return DashRequestDecision("malformed", reason="Submit control appears in callback state")
    states = {}
    for item in state:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("property"), str):
            return DashRequestDecision("malformed", reason="unexpected callback state entry")
        states[f"{item['id']}.{item['property']}"] = item.get("value")
    value = submit_inputs[0].get("value")
    if value is None or (type(value) is int and value == 0):
        return DashRequestDecision("submit_mount", clicks=0, states=states)
    if type(value) is int and value >= 1:
        return DashRequestDecision("submit_click", clicks=value, states=states)
    return DashRequestDecision("malformed", reason=f"unexpected n_clicks {value!r}")


def check_submit_callback_state(states: Mapping[str, Any], expected: PortalConfig) -> list[str]:
    """Compare the form state carried by a submit callback with the canonical configuration."""
    if expected is None:
        return ["no expected configuration"]
    missing = [key for key in SUBMIT_CALLBACK_STATE_KEYS if key not in states]
    if missing:
        return [f"callback lacks {', '.join(missing)}"]
    problems = []
    if states["date-range-picker.value"] != [expected.startdate, expected.stopdate]:
        problems.append(f"date-range-picker {states['date-range-picker.value']!r} != {[expected.startdate, expected.stopdate]!r}")
    for key, wanted in (("tag_select.data", expected.filters), ("blocks_select.data", expected.blocks)):
        value = states[key]
        if value is None:
            value = []
        if not isinstance(value, list) or sorted(map(str, value)) != sorted(wanted) or len(set(map(str, value))) != len(value):
            problems.append(f"{key} {states[key]!r} != {list(wanted)!r}")
    if states["field_select.value"] != list(expected.content):
        problems.append(f"field_select {states['field_select.value']!r} != {list(expected.content)!r}")
    extra = states["extra_cond.value"]
    if extra is not None and not isinstance(extra, str):
        problems.append(f"extra_cond {extra!r} is not text")
    elif normalize_extra_cond(extra) != normalize_extra_cond(expected.extra_cond):
        problems.append("extra_cond carries SQL conditions")
    if expected.catalog_filename is None and (states["object-catalog.data"] is not None or states["upload-data.filename"] is not None):
        problems.append("a catalogue is attached")
    return problems


def _references(value: Any, name: str) -> bool:
    if isinstance(value, str):
        return name in value
    if isinstance(value, dict):
        return any(_references(key, name) or _references(item, name) for key, item in value.items())
    if isinstance(value, list):
        return any(_references(item, name) for item in value)
    return False


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
    context_id: Optional[str]  # current browser-context generation; None when closed
    live_submit_enabled: bool

    def open(self) -> None: ...

    def upload_config(self, path: Path) -> None: ...

    def observe_form(self) -> PortalFormObservation: ...

    def reach_final_review(self) -> FinalReviewObservation: ...

    def download_config(self, destination_dir: Path) -> Path: ...

    def submit(self, authorization: SubmitAuthorization) -> SubmitObservation: ...

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


def scientific_state_from_observation(observation: PortalFormObservation) -> PortalScientificState:
    """Canonical scientific state of the visible form (unreadable dates become empty)."""
    dates = observed_dates(observation)
    start, stop = (dates[0], dates[-1]) if len(dates) in (1, 2) else ("", "")
    return PortalScientificState(
        startdate=start,
        stopdate=stop,
        content=tuple(observation.content_values),
        filters=tuple(observation.selected_filter_buttons),
        blocks=tuple(observation.selected_block_buttons),
        extra_cond=normalize_extra_cond(observation.extra_cond_text),
        catalog_none="no catalog" in observation.catalog_label.lower(),
    )
