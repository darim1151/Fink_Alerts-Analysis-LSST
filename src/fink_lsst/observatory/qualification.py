"""Read-only scientific state reconstruction from replayed acquisition evidence."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from fink_lsst.acquisition.registry import AcquisitionRegistry
from fink_lsst.acquisition.states import AcquisitionState
from fink_lsst.analytics.catalog import AdmissionError, validate_cohort
from fink_lsst.analytics.contract import CONTRACT_ID
from fink_lsst.data_root import confine_tree, validate_path_component

from .model import (
    AnalyticalAdmission as A,
)
from .model import (
    CapabilityState as C,
)
from .model import (
    CharacterizationState as H,
)
from .model import (
    Qualification,
)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_definition(path, repo_root):
    """Validate a declared cohort without mistaking declaration for admission."""
    path = confine_tree(path, Path(repo_root).resolve() / "configs/analysis_cohorts")
    try:
        definition = json.loads(path.read_text())
        if definition["contract_id"] != CONTRACT_ID:
            raise AdmissionError("unsupported or unnamed analytical cohort")
        validate_path_component(definition["cohort_name"], "cohort_name")
        items = definition["acquisitions"]
        if not isinstance(items, list) or not items:
            raise AdmissionError("cohort must contain acquisitions")
        records = []
        registry = AcquisitionRegistry(Path(repo_root) / "configs/acquisitions")
        for item in items:
            record = registry.load(item["acquisition_id"])
            if record.state != AcquisitionState.DELIVERY_VALIDATED:
                raise AdmissionError(
                    "declared cohort acquisition is not DELIVERY_VALIDATED"
                )
            if (
                record.request.science_profile != "lsst_light_static_all_alerts_v1"
                or record.request.survey != "lsst"
            ):
                raise AdmissionError("declared cohort profile incompatible")
            reference = item.get("characterization")
            if reference is not None:
                validate_path_component(reference["run_id"], "characterization run_id")
                if not re.fullmatch(
                    "[0-9a-f]{64}", reference["summary_sha256"]
                ) or not re.fullmatch("[0-9a-f]{40}", reference["code_sha"]):
                    raise AdmissionError("malformed characterization pin")
            # Reuse the native overlap/identity-window admission rules.
            records.append(record)
        from types import SimpleNamespace

        validate_cohort(
            [
                SimpleNamespace(
                    acquisition_id=r.acquisition_id,
                    topic=r.topic,
                    start=r.request.start,
                    stop=r.request.stop,
                )
                for r in records
            ]
        )
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AdmissionError("malformed cohort definition") from exc
    return definition


def qualification(record, reference=None, verified=False, admitted=False):
    if verified and not reference:
        raise AdmissionError("verified characterization needs its pinned identity")
    if admitted and not verified:
        raise AdmissionError(
            "Observatory science requires verified pinned characterization"
        )
    state = (
        H.CHARACTERIZED
        if verified
        else H.REFERENCE_PINNED
        if reference
        else H.NOT_ESTABLISHED
    )
    admission = (
        A.ANALYTICALLY_ADMITTED
        if admitted
        else A.COHORT_DECLARED_NOT_VERIFIED
        if reference
        else A.NOT_ADMITTED
    )
    reasons = (
        "completed catalog and pinned characterization verified; baseline delivery-window science only"
        if admitted
        else "characterization/cohort reference pinned; artifacts and catalog not verified here"
        if reference
        else "no accepted characterization/cohort admission evidence"
    )
    evidence = [record.entries[-1].entry_sha256]
    if reference:
        evidence.append(reference["summary_sha256"])
    return Qualification(
        record.state.value,
        state,
        admission,
        C.PARTIALLY_QUALIFIED if admitted else C.UNAVAILABLE,
        reasons,
        tuple(evidence),
    )


def delivery_rows(record):
    entry = record.last_entry(AcquisitionState.DELIVERY_VALIDATED)
    return entry.evidence["receipt"]["readable_rows"] if entry else None
