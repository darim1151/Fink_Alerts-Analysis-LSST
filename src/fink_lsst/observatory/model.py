"""Fink-native, derived Observatory products; no cross-domain wire contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class CapabilityState(str, Enum):
    AVAILABLE = "AVAILABLE"
    PARTIALLY_QUALIFIED = "PARTIALLY_QUALIFIED"
    UNAVAILABLE = "UNAVAILABLE"


class CharacterizationState(str, Enum):
    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    REFERENCE_PINNED = "REFERENCE_PINNED"
    CHARACTERIZED = "CHARACTERIZED"


class AnalyticalAdmission(str, Enum):
    NOT_ADMITTED = "NOT_ADMITTED"
    COHORT_DECLARED_NOT_VERIFIED = "COHORT_DECLARED_NOT_VERIFIED"
    ANALYTICALLY_ADMITTED = "ANALYTICALLY_ADMITTED"


@dataclass(frozen=True)
class Qualification:
    transport_state: str
    characterization_state: CharacterizationState
    analytical_admission: AnalyticalAdmission
    capability_state: CapabilityState
    reason: str
    evidence: Tuple[str, ...]

    def __post_init__(self):
        if not self.reason or not self.evidence:
            raise ValueError("qualification needs a reason and evidence")
        admitted = (
            self.analytical_admission == AnalyticalAdmission.ANALYTICALLY_ADMITTED
        )
        if admitted and (
            self.transport_state != "DELIVERY_VALIDATED"
            or self.characterization_state != CharacterizationState.CHARACTERIZED
        ):
            raise ValueError(
                "admission requires validated delivery and verified characterization"
            )
        if not admitted and self.capability_state != CapabilityState.UNAVAILABLE:
            raise ValueError("unadmitted science capabilities must remain unavailable")


@dataclass(frozen=True)
class AcquisitionBasis:
    acquisition_id: str
    topic: Optional[str]
    requested_start_utc: str
    requested_stop_utc: str
    validated_delivery_rows: Optional[int]
    qualification: Qualification
    provenance_ref: str


@dataclass(frozen=True)
class FinkObservatoryBasis:
    cohort_name: Optional[str]
    cohort_sha256: Optional[str]
    acquisitions: Tuple[AcquisitionBasis, ...]
    coverage_meaning: str = (
        "delivered Fink sources in admitted cohort; no Rubin completeness claim"
    )


@dataclass(frozen=True)
class FinkProvenanceRecord:
    provenance_id: str
    acquisition_id: str
    cohort_name: Optional[str]
    cohort_sha256: Optional[str]
    characterization: dict
    analysis_contract_id: str
    analysis_contract_sha256: str
    input_build_id: Optional[str]
    input_catalog_sha256: Optional[str]
    input_build_code_sha: Optional[str]
    adapter_code_sha: str
    evidence: dict
    authority: str = "DERIVED / NON-AUTHORITATIVE"


@dataclass(frozen=True)
class FinkFeatureDefinition:
    name: str
    namespace: str
    entity_level: str
    dtype: str
    unit: Optional[str]
    time_semantics: str
    definition: str
    provenance_refs: Tuple[str, ...]
    coverage: dict
    qualification: CapabilityState
    reason: str
    evidence: Tuple[str, ...]
    laboratory_role: Optional[str] = None
    module_provenance: Optional[dict] = None


@dataclass(frozen=True)
class FinkCapabilityManifest:
    entries: Tuple[FinkFeatureDefinition, ...]

    def discover_dimensions(self):
        return tuple(
            x
            for x in self.entries
            if x.laboratory_role and x.qualification != CapabilityState.UNAVAILABLE
        )


@dataclass(frozen=True)
class FinkTemporalProduct:
    native_field: str
    native_format: str
    native_scale: str
    observation_bins_tai: Tuple[dict, ...]
    utc_date_bins: Optional[Tuple[dict, ...]]
    delivery_windows: Tuple[dict, ...]
    broker_delivery_status: Tuple[dict, ...]
    analytical_cohort_acquisitions: Tuple[str, ...]
    provenance_refs: Tuple[str, ...]
    meaning: str = "observation distribution of delivered sources; bins are not Rubin observing nights"
    utc_conversion: Optional[str] = None


@dataclass(frozen=True)
class FinkSkyProduct:
    cell_scheme: str
    order: int
    coordinate_semantics: str
    cells: Tuple[dict, ...]
    input_rows: Optional[int]
    excluded_position_rows: Optional[int]
    provenance_refs: Tuple[str, ...]
    meaning: str = "delivered-source density; not survey footprint or exposure coverage"


@dataclass(frozen=True)
class FinkEntityRecord:
    entity_type: str
    native_ids: dict
    population: str
    fields: dict
    broker_snapshot_refs: Tuple[str, ...]
    qualified_feature_refs: Tuple[str, ...]
    provenance_refs: Tuple[str, ...]
    authority: str = "DERIVED / NON-AUTHORITATIVE"


@dataclass(frozen=True)
class BrokerSnapshot:
    snapshot_id: str
    native_source_id: str
    acquisition_id: str
    observation_mjd_tai: float
    modules: dict
    original_module_sha256: dict
    nonfinite_paths: Tuple[str, ...]
    provenance_refs: Tuple[str, ...]
    meaning: str = "source-time broker output; no timeless object classification or probability calibration"


@dataclass(frozen=True)
class FinkObservatoryDomainModel:
    product_kind: str
    basis: FinkObservatoryBasis
    capabilities: FinkCapabilityManifest
    temporal: FinkTemporalProduct
    sky: FinkSkyProduct
    entities: Tuple[FinkEntityRecord, ...]
    broker_snapshots: Tuple[BrokerSnapshot, ...]
    features: Tuple[FinkFeatureDefinition, ...]
    provenance: Tuple[FinkProvenanceRecord, ...]
    sampling: dict
    native_model_version: str = "fink_observatory_native_v1"
    authority: str = "DERIVED / NON-AUTHORITATIVE"
