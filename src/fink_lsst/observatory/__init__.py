"""Scientific Fink domain adapter, independent of the shared Observatory schema."""

from .adapter import build_from_catalog, metadata_fixture
from .model import FinkObservatoryDomainModel
from .serialization import (
    canonical_bytes,
    native_artifact,
    serialize_observatory_bundle_v1,
)

__all__ = [
    "build_from_catalog",
    "metadata_fixture",
    "FinkObservatoryDomainModel",
    "canonical_bytes",
    "native_artifact",
    "serialize_observatory_bundle_v1",
]
