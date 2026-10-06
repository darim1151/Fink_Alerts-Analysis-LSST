"""Bind accepted native analytical catalogs to derived scientific products.

This module reads registry/cohort/characterization/catalog evidence. It never
acquires, admits a new cohort, changes authority, or writes scientific inputs.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from fink_lsst.acquisition.registry import AcquisitionRegistry
from fink_lsst.acquisition.states import AcquisitionState
from fink_lsst.analytics.catalog import (
    AdmissionError,
    digest_json,
    load_cohort,
    open_catalog,
)
from fink_lsst.analytics.contract import CONTRACT_ID, analytical_contract
from fink_lsst.characterization import raw_snapshot
from fink_lsst.data_root import (
    REPO_ROOT,
    confine_tree,
    storage_base,
    validate_path_component,
)

from .capabilities import registry
from .model import (
    AcquisitionBasis,
    FinkObservatoryBasis,
    FinkObservatoryDomainModel,
    FinkProvenanceRecord,
)
from .products import entities, sky, temporal
from .qualification import delivery_rows, file_sha256, qualification, read_definition


def code_identity(repo_root):
    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(repo_root), *args], text=True
        ).strip()

    if git("status", "--porcelain"):
        raise AdmissionError("export requires clean committed code/cohort")
    return git("rev-parse", "HEAD")


def _require_sha(value, length, label):
    if not isinstance(value, str) or not re.fullmatch(
        "[0-9a-f]{" + str(length) + "}", value
    ):
        raise AdmissionError("invalid " + label)
    return value


def _tracked(path, repo):
    subprocess.check_output(
        [
            "git",
            "-C",
            str(repo),
            "ls-files",
            "--error-unmatch",
            str(Path(path).resolve().relative_to(repo)),
        ],
        stderr=subprocess.STDOUT,
    )


def _basis(repo, definition, code_sha, admitted_inputs=(), build=None):
    declared = {
        x["acquisition_id"]: x.get("characterization")
        for x in definition["acquisitions"]
    }
    admitted = {x.acquisition_id: x for x in admitted_inputs}
    basis, provenance = [], []
    cohort_hash = digest_json(definition)
    contract_hash = digest_json(analytical_contract())
    for record in AcquisitionRegistry(repo / "configs/acquisitions").list_records():
        aid = record.acquisition_id
        item = admitted.get(aid)
        reference = declared.get(aid)
        pref = "fink-provenance/" + aid
        evidence = dict(
            acquisition_log_entry_sha256=record.entries[-1].entry_sha256,
            request_sha256=record.request_sha256,
            request_file_sha256=file_sha256(record.directory / "request.json"),
            state_log_file_sha256=file_sha256(record.directory / "state_log.jsonl"),
            source_schema_fixture_sha256=file_sha256(
                repo / "data/fixtures/schema/fink_lsst_schema_sources.json"
            ),
            qualification_scope="verified build" if item else "committed metadata only",
        )
        entry = record.last_entry(AcquisitionState.DELIVERY_VALIDATED)
        if entry:
            evidence.update(
                delivery_receipt_canonical_sha256=digest_json(
                    entry.evidence["receipt"]
                ),
                delivery_receipt_file_ref=entry.evidence["receipt_ref"],
                inventory_sha256=entry.evidence["receipt"]["inventory_sha256"],
                inventory_root_sha256=entry.evidence["receipt"][
                    "inventory_root_sha256"
                ],
            )
        if item:
            evidence.update(
                raw_stat_fingerprint=item.snapshot["stat_fingerprint"],
                schema_groups=item.receipt["schema_groups"],
                analysis_manifest_sha256=build["manifest_sha256"],
            )
        characterization = dict(item.linkage) if item else dict(reference or {})
        characterization["verification"] = (
            "ARTIFACTS_VERIFIED"
            if item
            else "REFERENCE_ONLY"
            if reference
            else "NOT_ESTABLISHED"
        )
        if reference:
            evidence["cohort_definition_file_sha256"] = (
                build["cohort_file_sha256"] if build else None
            )
        provenance.append(
            FinkProvenanceRecord(
                pref,
                aid,
                definition["cohort_name"] if aid in declared else None,
                cohort_hash if aid in declared else None,
                characterization,
                CONTRACT_ID,
                contract_hash,
                build["run_id"] if item else None,
                build["catalog_sha256"] if item else None,
                build["code_commit_sha"] if item else None,
                code_sha,
                evidence,
            )
        )
        basis.append(
            AcquisitionBasis(
                aid,
                record.topic,
                record.request.start,
                record.request.stop,
                delivery_rows(record),
                qualification(
                    record, reference, verified=bool(item), admitted=bool(item)
                ),
                pref,
            )
        )
    if set(admitted) - {x.acquisition_id for x in basis}:
        raise AdmissionError("catalog acquisition not in replayed registry")
    return FinkObservatoryBasis(
        definition["cohort_name"], cohort_hash, tuple(basis)
    ), tuple(provenance)


def _model(
    basis,
    provenance,
    conn=None,
    inputs=(),
    include_utc=False,
    sky_order=3,
    sample_per_population=12,
):
    admitted_refs = tuple(
        x.provenance_id for x in provenance if x.input_build_id is not None
    )
    refs = admitted_refs if conn else tuple(x.provenance_id for x in provenance)
    inventories = tuple(
        group["field_inventory"] for x in inputs for group in x.compatibility["groups"]
    )
    coverage = tuple(
        row for x in inputs for row in x.characterization["critical_field_coverage"]
    )
    capabilities = registry(refs, conn, inventories, coverage)
    source_records, snapshots = entities(
        conn,
        {x.acquisition_id: x.provenance_id for x in provenance},
        capabilities,
        sample_per_population,
    )
    return FinkObservatoryDomainModel(
        "SCIENTIFIC_DERIVED" if conn else "METADATA / CONTRACT FIXTURE",
        basis,
        capabilities,
        temporal(conn, basis, refs, include_utc),
        sky(conn, refs, sky_order),
        source_records,
        snapshots,
        capabilities.discover_dimensions(),
        provenance,
        dict(
            method="smallest native source IDs separately per DIA/SSO/AMBIGUOUS; smallest DIA grouping IDs",
            maximum_per_population=sample_per_population if conn else 0,
            maximum_derived_dia_objects=sample_per_population if conn else 0,
            representative_only=True,
            population_representativeness="NOT_ESTABLISHED",
            int64_id_encoding="decimal strings; no cross-broker universal identity",
            snapshot_nonfinite_policy="null with exact paths plus original module text SHA256",
        ),
    )


def metadata_fixture(repo_root=REPO_ROOT, cohort_path=None):
    """Committed metadata only: no sources, sky counts or scientific time bins."""
    repo = Path(repo_root).resolve()
    code_sha = _require_sha(code_identity(repo), 40, "adapter code SHA")
    path = cohort_path or repo / "configs/analysis_cohorts/month1.json"
    _tracked(path, repo)
    definition = read_definition(path, repo)
    basis, provenance = _basis(repo, definition, code_sha)
    # Schema/contract metadata supplies definitions, never fabricated observations.
    for item in provenance:
        item.evidence["cohort_definition_file_sha256"] = (
            file_sha256(path) if item.cohort_name else None
        )
    return _model(basis, provenance)


def build_from_catalog(
    catalog_path,
    data_root,
    cohort_path,
    repo_root=REPO_ROOT,
    include_utc=False,
    sky_order=3,
    sample_per_population=12,
):
    """Export only a completed, pinned, characterized analytical build.

    Reuses native fail-closed characterization and reopening checks; validates
    build artifact hashes and exact cohort/contract lineage before any queries.
    """
    repo, root = Path(repo_root).resolve(), Path(data_root).resolve()
    code_sha = _require_sha(code_identity(repo), 40, "adapter code SHA")
    _tracked(cohort_path, repo)
    definition = read_definition(cohort_path, repo)
    if any(not x.get("characterization") for x in definition["acquisitions"]):
        raise AdmissionError(
            "Observatory science requires pinned characterization for every acquisition"
        )
    path = confine_tree(catalog_path, storage_base(root, "data/processed"))
    if path.name != "analytics.duckdb":
        raise AdmissionError("expected native analytics.duckdb build artifact")
    run_id = validate_path_component(path.parent.name, "input build run_id")
    if path.parent != storage_base(root, "data/processed") / run_id:
        raise AdmissionError("catalog must be in its exact run directory")
    manifest_path = confine_tree(
        storage_base(root, "manifests") / run_id / "manifest.json",
        storage_base(root, "manifests"),
    )
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
        if (
            manifest["completion_status"] not in ("PASS", "PASS_WITH_LIMITATIONS")
            or not manifest["finished_utc"]
        ):
            raise AdmissionError("analytical build incomplete or failed")
        if manifest["run_id"] != run_id or manifest["contract_id"] != CONTRACT_ID:
            raise AdmissionError("analytical build identity/contract mismatch")
        _require_sha(manifest["code_commit_sha"], 40, "analytical build code SHA")
        if manifest["cohort"] != definition or manifest["cohort_sha256"] != digest_json(
            definition
        ):
            raise AdmissionError("analytical build differs from checked-in cohort")
        if manifest["processed_dir"] != str(path.parent) or manifest[
            "output_dir"
        ] != str(storage_base(root, "outputs") / run_id):
            raise AdmissionError("analytical build path binding differs")
        artifacts = manifest["artifact_sha256"]
        if str(path) not in artifacts:
            raise AdmissionError("catalog artifact hash absent")
        before_hashes = {}
        for name, expected in sorted(artifacts.items()):
            artifact = Path(name)
            base = (
                path.parent
                if path.parent in artifact.parents
                else storage_base(root, "outputs") / run_id
            )
            artifact = confine_tree(artifact, base)
            _require_sha(expected, 64, "build artifact SHA256")
            before_hashes[str(artifact)] = file_sha256(artifact)
            if before_hashes[str(artifact)] != expected:
                raise AdmissionError("analytical build artifact hash differs")
        inputs, checked_definition = load_cohort(cohort_path, root, repo)
        if checked_definition != definition:
            raise AdmissionError("cohort changed during export")
        expected_inputs = sorted(
            (x.provenance() for x in inputs), key=lambda x: x["acquisition_id"]
        )
        if (
            sorted(manifest["acquisitions"], key=lambda x: x["acquisition_id"])
            != expected_inputs
        ):
            raise AdmissionError(
                "build acquisition/characterization provenance differs"
            )
        build = dict(
            run_id=run_id,
            code_commit_sha=manifest["code_commit_sha"],
            catalog_sha256=artifacts[str(path)],
            manifest_sha256=file_sha256(manifest_path),
            cohort_file_sha256=file_sha256(cohort_path),
        )
        with open_catalog(path, root) as conn:
            stored = {
                key: json.loads(value)
                for key, value in conn.execute(
                    "SELECT key,value FROM catalog_metadata"
                ).fetchall()
            }
            if (
                stored["run_id"] != run_id
                or stored["code_sha"] != build["code_commit_sha"]
                or stored["contract"] != analytical_contract()
                or sorted(stored["inputs"], key=lambda x: x["acquisition_id"])
                != expected_inputs
            ):
                raise AdmissionError(
                    "catalog metadata binding differs from qualified build"
                )
            basis, provenance = _basis(repo, definition, code_sha, inputs, build)
            model = _model(
                basis,
                provenance,
                conn,
                inputs,
                include_utc,
                sky_order,
                sample_per_population,
            )
        for item in inputs:
            if raw_snapshot(item.raw) != item.snapshot:
                raise AdmissionError(
                    "raw stat fingerprint changed during Observatory export"
                )
        if (
            manifest_path.read_bytes() != manifest_bytes
            or file_sha256(cohort_path) != build["cohort_file_sha256"]
        ):
            raise AdmissionError("build/cohort evidence changed during export")
        if any(
            file_sha256(name) != expected for name, expected in before_hashes.items()
        ):
            raise AdmissionError("build artifact changed during export")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AdmissionError("malformed analytical build evidence") from exc
    return model
