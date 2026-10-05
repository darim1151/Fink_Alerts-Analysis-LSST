"""Request planner and canonical acquisition specification (layers B and C).

Date semantics
--------------
The repository describes scientific windows as half-open UTC calendar dates,
`[start, stop)`: `start` is the first observing night requested and `stop` is
the first night *not* requested. The Fink Data Transfer portal date picker is
inclusive on both ends ("Pick up start and stop dates (included)"). So:

    repository [2026-02-25, 2026-03-25)  ->  portal 2026-02-25 .. 2026-03-24

A one-night request `[d, d+1)` becomes the portal range `d .. d`.

Scientific identity vs operational metadata
-------------------------------------------
The fingerprint is the SHA-256 of the canonical JSON (sorted keys, no
whitespace, ASCII) of the scientific identity only: survey, science profile
name and pinned content digest, scope and the half-open window. Timestamps,
machine, invocation path and file formatting never enter it, so equivalent
requests always hash identically and different windows or profile versions
always differ.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Tuple, Union

from fink_lsst.data_root import validate_path_component

from .profile import PRODUCTION_PROFILE_NAME, ScienceProfile, resolve_profile


REQUEST_SCHEMA_VERSION = 1
IDENTITY_SCHEMA_VERSION = 1
WINDOW_SEMANTICS = "half_open_utc_dates"
# Operational guard against typos (a wrong year would order a huge job). Not a
# scientific limit; a longer window is a deliberate code change.
MAX_WINDOW_NIGHTS = 92
SINGLE_NIGHT_SCOPE = "full_night"
DATE_RANGE_SCOPE = "date_range"
GENERATOR = "fink_lsst.acquisition"
# Requested nights must have ended at least this many days before `as_of`
# (UTC), so Fink ingestion of the last night has settled.
SETTLING_DAYS = 1
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# The only keys a request specification may carry; anything else could be an
# attempt to alter the science profile and is rejected.
_REQUEST_MAPPING_KEYS = frozenset({"start", "stop", "science_profile", "created_at_utc"})


class PlanningError(ValueError):
    """Raised when a requested window or request specification is invalid."""


def parse_utc_date(value: Any, label: str) -> date:
    """Parse a strict `YYYY-MM-DD` string or a `datetime.date` (not a datetime)."""
    if isinstance(value, datetime):
        raise PlanningError(f"{label} must be a calendar date, not a datetime: {value!r}")
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        raise PlanningError(f"{label} must be a UTC calendar date written YYYY-MM-DD, got {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PlanningError(f"{label} is not a valid calendar date: {value!r}") from exc


@dataclass(frozen=True)
class AcquisitionWindow:
    """A half-open UTC date window `[start, stop)` of whole observing nights."""

    start: date
    stop: date

    @classmethod
    def from_values(cls, start: Any, stop: Any, max_nights: int = MAX_WINDOW_NIGHTS) -> "AcquisitionWindow":
        start_date = parse_utc_date(start, "start")
        stop_date = parse_utc_date(stop, "stop")
        if stop_date <= start_date:
            raise PlanningError(f"stop must be after start: [{start_date}, {stop_date}) contains no night")
        nights = (stop_date - start_date).days
        if nights > max_nights:
            raise PlanningError(f"a request may cover at most {max_nights} nights, got {nights}")
        return cls(start_date, stop_date)

    @property
    def nights(self) -> Tuple[str, ...]:
        count = (self.stop - self.start).days
        return tuple((self.start + timedelta(days=offset)).isoformat() for offset in range(count))

    @property
    def portal_startdate(self) -> str:
        return self.start.isoformat()

    @property
    def portal_stopdate(self) -> str:
        """Inclusive portal stop date: the last requested night."""
        return (self.stop - timedelta(days=1)).isoformat()

    @property
    def component(self) -> str:
        return f"{self.start.isoformat()}_to_{self.stop.isoformat()}"

    @property
    def scope(self) -> str:
        return SINGLE_NIGHT_SCOPE if len(self.nights) == 1 else DATE_RANGE_SCOPE


@dataclass(frozen=True)
class OperationalMetadata:
    """Facts about how a request was made. Never part of the fingerprint."""

    created_at_utc: str
    generator: str = GENERATOR
    as_of_date: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {"created_at_utc": self.created_at_utc, "generator": self.generator, "as_of_date": self.as_of_date}


@dataclass(frozen=True)
class AcquisitionRequest:
    """Deterministic, canonical description of one scientific acquisition."""

    schema_version: int
    acquisition_id: str
    fingerprint: str
    science_profile: str
    profile_content_sha256: str
    survey: str
    scope: str
    start: str
    stop: str
    portal_startdate: str
    portal_stopdate: str
    expected_dates: Tuple[str, ...]
    operational: OperationalMetadata = field(compare=False)

    @property
    def window(self) -> AcquisitionWindow:
        return AcquisitionWindow.from_values(self.start, self.stop)

    @property
    def profile(self) -> ScienceProfile:
        return resolve_profile(self.science_profile)

    def expected_raw_dir(self, topic: str) -> str:
        """Data-root-relative raw delivery directory for `topic` (same rule as the run manifests)."""
        topic = validate_path_component(topic, "topic")
        return f"data/raw/data_transfer/{self.scope}/{self.window.component}/{topic}"

    def scientific_identity(self) -> dict[str, Any]:
        return {
            "identity_schema": IDENTITY_SCHEMA_VERSION,
            "survey": self.survey,
            "science_profile": self.science_profile,
            "profile_content_sha256": self.profile_content_sha256,
            "scope": self.scope,
            "window": {"start": self.start, "stop": self.stop, "semantics": WINDOW_SEMANTICS},
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "acquisition_id": self.acquisition_id,
            "fingerprint": self.fingerprint,
            "fingerprint_algorithm": "sha256(canonical_json(scientific_identity))",
            "scientific_identity": self.scientific_identity(),
            "portal_dates_inclusive": {"startdate": self.portal_startdate, "stopdate": self.portal_stopdate},
            "expected_dates": list(self.expected_dates),
            "operational": self.operational.to_dict(),
        }


def canonical_json(value: Any) -> str:
    """Return the canonical JSON text used for fingerprints."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def fingerprint_identity(identity: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()


def acquisition_id_for(window: AcquisitionWindow, profile: ScienceProfile, fingerprint: str) -> str:
    """Deterministic, path-safe acquisition id derived from the scientific identity."""
    return validate_path_component(f"acq_{profile.short_tag}_{window.component}_{fingerprint[:12]}", "acquisition_id")


def build_acquisition_request(
    start: Any,
    stop: Any,
    *,
    profile: Union[str, ScienceProfile] = PRODUCTION_PROFILE_NAME,
    as_of: Optional[date] = None,
    created_at_utc: Optional[str] = None,
    generator: str = GENERATOR,
) -> AcquisitionRequest:
    """Plan a request for `[start, stop)` under a fixed science profile.

    `as_of` (default: today, UTC) rejects windows whose last night has not
    finished and settled (`SETTLING_DAYS`); it is checked here, not stored
    in the scientific identity.
    """
    resolved = resolve_profile(profile)
    window = AcquisitionWindow.from_values(start, stop)
    as_of_date = as_of or datetime.now(timezone.utc).date()
    latest_stop = as_of_date - timedelta(days=SETTLING_DAYS)
    if window.stop > latest_stop:
        raise PlanningError(
            f"night {window.portal_stopdate} has not finished and settled as of {as_of_date.isoformat()} (UTC); "
            f"the last requestable night is {(latest_stop - timedelta(days=1)).isoformat()}"
        )
    created = created_at_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _assemble(resolved, window, OperationalMetadata(created_at_utc=created, generator=generator, as_of_date=as_of_date.isoformat()))


def request_from_mapping(mapping: Mapping[str, Any], *, as_of: Optional[date] = None) -> AcquisitionRequest:
    """Build a request from a loosely formatted mapping (for example a YAML file).

    Only `start`, `stop`, optionally `science_profile`, and the operational
    `created_at_utc` are accepted. Any other key is rejected, because it could
    be an attempt to change the science profile.
    """
    if not isinstance(mapping, Mapping):
        raise PlanningError("request specification must be a mapping")
    unknown = sorted(str(key) for key in mapping if key not in _REQUEST_MAPPING_KEYS)
    if unknown:
        raise PlanningError(f"request may only give {sorted(_REQUEST_MAPPING_KEYS)}; {', '.join(unknown)} could alter the science profile (create a new profile version instead)")
    return build_acquisition_request(
        mapping.get("start"),
        mapping.get("stop"),
        profile=mapping.get("science_profile", PRODUCTION_PROFILE_NAME),
        as_of=as_of,
        created_at_utc=str(mapping["created_at_utc"]) if mapping.get("created_at_utc") else None,
    )


def request_from_dict(payload: Mapping[str, Any]) -> AcquisitionRequest:
    """Rebuild a stored request and verify every derived field against a fresh derivation."""
    try:
        identity = payload["scientific_identity"]
        window_payload = identity["window"]
        operational = payload.get("operational") or {}
        rebuilt = _assemble(
            resolve_profile(identity["science_profile"]),
            AcquisitionWindow.from_values(window_payload["start"], window_payload["stop"]),
            OperationalMetadata(
                created_at_utc=str(operational.get("created_at_utc")),
                generator=str(operational.get("generator", GENERATOR)),
                as_of_date=operational.get("as_of_date"),
            ),
        )
    except (KeyError, TypeError) as exc:
        raise PlanningError(f"stored request is incomplete: {exc}") from exc
    if int(payload.get("schema_version", -1)) != REQUEST_SCHEMA_VERSION:
        raise PlanningError(f"unsupported request schema_version {payload.get('schema_version')!r}")
    stored = {key: value for key, value in payload.items() if key != "operational"}
    expected = {key: value for key, value in rebuilt.to_dict().items() if key != "operational"}
    if canonical_json(stored) != canonical_json(expected):
        differing = sorted(key for key in set(stored) | set(expected) if canonical_json(stored.get(key)) != canonical_json(expected.get(key)))
        raise PlanningError(f"stored request does not match its scientific identity: {', '.join(differing)}")
    return rebuilt


def _assemble(profile: ScienceProfile, window: AcquisitionWindow, operational: OperationalMetadata) -> AcquisitionRequest:
    identity = {
        "identity_schema": IDENTITY_SCHEMA_VERSION,
        "survey": profile.survey,
        "science_profile": profile.name,
        "profile_content_sha256": profile.content_sha256(),
        "scope": window.scope,
        "window": {"start": window.start.isoformat(), "stop": window.stop.isoformat(), "semantics": WINDOW_SEMANTICS},
    }
    fingerprint = fingerprint_identity(identity)
    return AcquisitionRequest(
        schema_version=REQUEST_SCHEMA_VERSION,
        acquisition_id=acquisition_id_for(window, profile, fingerprint),
        fingerprint=fingerprint,
        science_profile=profile.name,
        profile_content_sha256=profile.content_sha256(),
        survey=profile.survey,
        scope=window.scope,
        start=window.start.isoformat(),
        stop=window.stop.isoformat(),
        portal_startdate=window.portal_startdate,
        portal_stopdate=window.portal_stopdate,
        expected_dates=window.nights,
        operational=operational,
    )
