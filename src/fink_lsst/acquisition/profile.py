"""Versioned scientific acquisition profiles (layer A).

A science profile pins *what* is requested from the Fink LSST Data Transfer
service: survey, packet and every filtering knob the portal offers. The only
production profile in this release is `lsst_light_static_all_alerts_v1`:
LSST, Light static packet, no tags/filters, no blocks, no catalogue, no extra
SQL. The date window is not part of a profile; it is supplied per request.

Changing scientific content requires a new profile name/version. The content
digest of every registered profile is pinned below, so editing a definition
in place (or handing in a `dataclasses.replace` copy) fails closed instead of
silently producing a different request under an old name.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple, Union


PRODUCTION_PROFILE_NAME = "lsst_light_static_all_alerts_v1"
LIGHT_STATIC_PACKET = "Light static packet"


class ProfileIntegrityError(ValueError):
    """Raised when a profile is unknown or its scientific content differs from its pinned version."""


@dataclass(frozen=True)
class ScienceProfile:
    name: str
    version: int
    short_tag: str
    survey: str
    packet: str
    filters: Tuple[str, ...]
    blocks: Tuple[str, ...]
    catalog_filename: Optional[str]
    extra_cond: Optional[str]
    description: str = ""

    def scientific_content(self) -> dict[str, Any]:
        """Return every field that changes what Fink delivers (the description does not)."""
        return {
            "name": self.name,
            "version": self.version,
            "survey": self.survey,
            "packet": self.packet,
            "filters": list(self.filters),
            "blocks": list(self.blocks),
            "catalog_filename": self.catalog_filename,
            "extra_cond": self.extra_cond,
        }

    def content_sha256(self) -> str:
        text = json.dumps(self.scientific_content(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


LSST_LIGHT_STATIC_ALL_ALERTS_V1 = ScienceProfile(
    name=PRODUCTION_PROFILE_NAME,
    version=1,
    short_tag="lsst_ls_v1",
    survey="lsst",
    packet=LIGHT_STATIC_PACKET,
    filters=(),
    blocks=(),
    catalog_filename=None,
    extra_cond=None,
    description=(
        "Every alert Fink returns for the LSST Light static packet over the requested nights, with no tag, "
        "filter, block, catalogue or SQL restriction. Light static is not non-SSO: it includes pred.is_sso rows."
    ),
)

# Pinned digests of each registered profile's scientific content. A new profile
# version gets a new name and a new entry; existing entries never change.
_PINNED_CONTENT_SHA256: Mapping[str, str] = MappingProxyType(
    {
        PRODUCTION_PROFILE_NAME: "62d809b0b24c6d88fa0d9259e59255e28794012958e8deebaf2e7a5c2723d173",
    }
)

SCIENCE_PROFILES: Mapping[str, ScienceProfile] = MappingProxyType({PRODUCTION_PROFILE_NAME: LSST_LIGHT_STATIC_ALL_ALERTS_V1})


def get_science_profile(name: str = PRODUCTION_PROFILE_NAME) -> ScienceProfile:
    """Return a registered profile after re-checking its pinned content digest."""
    profile = SCIENCE_PROFILES.get(name)
    if profile is None:
        raise ProfileIntegrityError(f"unknown science profile {name!r}; registered: {sorted(SCIENCE_PROFILES)}")
    _verify_pinned(profile)
    return profile


def resolve_profile(profile: Union[str, ScienceProfile]) -> ScienceProfile:
    """Resolve a profile name or object to the registered, integrity-checked profile.

    An object is accepted only if it is identical in scientific content to the
    registered profile of the same name; a modified copy is rejected.
    """
    if isinstance(profile, str):
        return get_science_profile(profile)
    if not isinstance(profile, ScienceProfile):
        raise ProfileIntegrityError(f"expected a profile name or ScienceProfile, got {type(profile).__name__}")
    registered = get_science_profile(profile.name)
    if profile.content_sha256() != registered.content_sha256():
        raise ProfileIntegrityError(
            f"profile {profile.name!r} was modified; changing scientific content requires a new profile version"
        )
    return registered


def _verify_pinned(profile: ScienceProfile) -> None:
    pinned = _PINNED_CONTENT_SHA256.get(profile.name)
    actual = profile.content_sha256()
    if pinned is None or actual != pinned:
        raise ProfileIntegrityError(
            f"profile {profile.name!r} content digest {actual} does not match its pinned digest {pinned}; "
            "create a new profile version instead of editing an existing one"
        )
