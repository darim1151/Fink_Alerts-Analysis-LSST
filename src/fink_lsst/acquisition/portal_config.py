"""Compile the Fink portal configuration from a canonical request (layer C).

The canonical `AcquisitionRequest` is authoritative; the portal YAML is a
compiled representation of it and is never accepted as user input. The
compiled text uses the same layout as the portal's own "Download
configuration" output, so a one-night request reproduces the G3A portal file
byte for byte.

`parse_portal_config` reads a configuration the portal returned or offered
for download, and `compare_portal_configs` checks it semantically (dates,
packet content, filters, blocks, catalogue, extra SQL) against the compiled
expectation. Unknown or missing keys are errors: a portal that starts
emitting something new must be reviewed, not silently accepted.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Optional, Tuple, Union

import yaml

from .planner import AcquisitionRequest, parse_utc_date, PlanningError
from .profile import LIGHT_STATIC_PACKET, PRODUCTION_PROFILE_NAME


PORTAL_CONFIG_KEYS = frozenset({"blocks", "catalog_filename", "content", "dates", "extra_cond", "filters"})
PORTAL_DATE_KEYS = frozenset({"startdate", "stopdate"})


class PortalConfigError(ValueError):
    """Raised when a portal configuration cannot be read as the expected structure."""


class PortalConfigMismatch(ValueError):
    """Raised when a portal configuration differs semantically from the intended request."""

    def __init__(self, mismatches: list[str]):
        super().__init__("portal configuration does not match the canonical request: " + "; ".join(mismatches))
        self.mismatches = list(mismatches)


@dataclass(frozen=True)
class PortalConfig:
    startdate: str
    stopdate: str
    content: Tuple[str, ...]
    filters: Tuple[str, ...]
    blocks: Tuple[str, ...]
    catalog_filename: Optional[str]
    extra_cond: Optional[str]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "blocks": list(self.blocks),
            "catalog_filename": self.catalog_filename,
            "content": list(self.content),
            "dates": {"startdate": self.startdate, "stopdate": self.stopdate},
            "extra_cond": self.extra_cond,
            "filters": list(self.filters),
        }


def compile_portal_config(request: AcquisitionRequest) -> PortalConfig:
    """Return the portal configuration for a canonical request."""
    if not isinstance(request, AcquisitionRequest):
        raise TypeError("compile_portal_config accepts only a canonical AcquisitionRequest")
    profile = request.profile  # re-verifies the pinned profile digest
    if profile.name != PRODUCTION_PROFILE_NAME:
        raise PortalConfigError(f"no portal compiler is defined for profile {profile.name!r}")
    if profile.packet != LIGHT_STATIC_PACKET or profile.filters or profile.blocks or profile.catalog_filename or profile.extra_cond:
        raise PortalConfigError("production profile no longer describes an unfiltered Light static request")
    window = request.window
    if (window.portal_startdate, window.portal_stopdate) != (request.portal_startdate, request.portal_stopdate):
        raise PortalConfigError("request portal dates do not follow from its half-open window")
    return PortalConfig(
        startdate=request.portal_startdate,
        stopdate=request.portal_stopdate,
        content=(profile.packet,),
        filters=tuple(profile.filters),
        blocks=tuple(profile.blocks),
        catalog_filename=profile.catalog_filename,
        extra_cond=profile.extra_cond,
    )


def render_portal_yaml(config: PortalConfig) -> str:
    """Render the configuration in the portal's own download layout."""
    return yaml.safe_dump(config.to_mapping(), sort_keys=True, default_flow_style=False)


def portal_config_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_portal_config(source: Union[str, bytes, Mapping[str, Any]]) -> PortalConfig:
    """Parse and normalize a portal configuration (YAML text or mapping)."""
    if isinstance(source, bytes):
        source = source.decode("utf-8")
    if isinstance(source, str):
        try:
            mapping = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            raise PortalConfigError(f"portal configuration is not valid YAML: {exc}") from exc
    else:
        mapping = source
    if not isinstance(mapping, Mapping):
        raise PortalConfigError("portal configuration must be a mapping")
    keys = set(mapping)
    if keys != PORTAL_CONFIG_KEYS:
        missing = sorted(PORTAL_CONFIG_KEYS - keys)
        unknown = sorted(str(key) for key in keys - PORTAL_CONFIG_KEYS)
        raise PortalConfigError(f"unexpected portal configuration keys (missing={missing}, unknown={unknown})")
    dates = mapping["dates"]
    if not isinstance(dates, Mapping) or set(dates) != PORTAL_DATE_KEYS:
        raise PortalConfigError("portal configuration `dates` must hold exactly startdate and stopdate")
    return PortalConfig(
        startdate=_normalize_date(dates["startdate"], "dates.startdate"),
        stopdate=_normalize_date(dates["stopdate"], "dates.stopdate"),
        content=_normalize_list(mapping["content"], "content"),
        filters=_normalize_list(mapping["filters"], "filters"),
        blocks=_normalize_list(mapping["blocks"], "blocks"),
        catalog_filename=_normalize_optional_text(mapping["catalog_filename"], "catalog_filename"),
        extra_cond=_normalize_optional_text(mapping["extra_cond"], "extra_cond"),
    )


def compare_portal_configs(expected: PortalConfig, observed: PortalConfig) -> list[str]:
    """Return human-readable semantic differences; an empty list means a match."""
    mismatches = []
    for name in ("startdate", "stopdate"):
        if getattr(expected, name) != getattr(observed, name):
            mismatches.append(f"dates.{name}: expected {getattr(expected, name)}, portal has {getattr(observed, name)}")
    if list(observed.content) != list(expected.content):
        mismatches.append(f"content: expected {list(expected.content)}, portal has {list(observed.content)}")
    for name in ("filters", "blocks"):
        expected_items, observed_items = getattr(expected, name), getattr(observed, name)
        if sorted(expected_items) != sorted(observed_items) or len(set(observed_items)) != len(observed_items):
            mismatches.append(f"{name}: expected {list(expected_items)}, portal has {list(observed_items)}")
    for name in ("catalog_filename", "extra_cond"):
        if getattr(expected, name) != getattr(observed, name):
            mismatches.append(f"{name}: expected {getattr(expected, name)!r}, portal has {getattr(observed, name)!r}")
    return mismatches


def assert_portal_config_matches(expected: PortalConfig, observed: PortalConfig) -> None:
    mismatches = compare_portal_configs(expected, observed)
    if mismatches:
        raise PortalConfigMismatch(mismatches)


def _normalize_date(value: Any, label: str) -> str:
    try:
        return parse_utc_date(value, label).isoformat() if isinstance(value, (str, date)) else _reject(label, value)
    except PlanningError as exc:
        raise PortalConfigError(str(exc)) from exc


def _normalize_list(value: Any, label: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PortalConfigError(f"{label} must be a list of strings, got {value!r}")
    return tuple(value)


def _normalize_optional_text(value: Any, label: str) -> Optional[str]:
    """Null, blank text, or a list of blank lines all mean "none".

    The portal writes `extra_cond: null` from a fresh form but `extra_cond: []`
    after a configuration upload (it keeps one condition per line); both are
    semantically no extra SQL. Any non-blank content is kept and compared.
    """
    if value is None:
        return None
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        value = "\n".join(item for item in value if item.strip())
    if not isinstance(value, str):
        raise PortalConfigError(f"{label} must be text, a list of lines, or null, got {value!r}")
    return value if value.strip() else None


def _reject(label: str, value: Any) -> str:
    raise PortalConfigError(f"{label} must be a YYYY-MM-DD date, got {value!r}")
